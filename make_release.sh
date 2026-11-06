#!/bin/bash
# Build a shippable release of SRT Generator.
#
#   ./make_release.sh 4.0                    → SRT-Generator-4.0-mac.zip (the .app, Apple Silicon)
#                                              + appcast.xml (Sparkle feed, EdDSA-signed zip)
#   ./make_release.sh 4.0 --full             → ...-mac-full.zip, the .app with models baked in
#   ./make_release.sh 4.0 --windows          → ...-win-lite.zip (x64 folder + Install.bat)
#   ./make_release.sh 4.0 --publish          → GitHub release v4.0 with both zips + appcast.xml
#                                              (build the mac and windows zips first)
#
# The version must match the VERSION file (the single source of version numbers);
# leave it out to use VERSION as is. Output lands in dist-release/. Downloads
# (uv, ffmpeg, Sparkle) are cached in release-assets/ so rebuilding is fast.
#
# Sparkle's EdDSA private key lives only in the login Keychain (account "srt-generator",
# created by Sparkle's generate_keys). sign_update reads it from there; it is never
# written to a file by this script.
set -euo pipefail
cd "$(dirname "$0")"

SPARKLE_VERSION="2.10.0"
SPARKLE_ACCOUNT="srt-generator"
REPO="brightlifecreations-projects/song-srt-generator"

FILE_VERSION="$(tr -d '[:space:]' < VERSION)"
VERSION="$FILE_VERSION"
if [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; then
  VERSION="$1"
  shift
fi
if [ "$VERSION" != "$FILE_VERSION" ]; then
  echo "!! VERSION file says ${FILE_VERSION}, not ${VERSION}. Edit VERSION first (it's the single source)."
  exit 1
fi
VARIANT="lite"
PLATFORM="mac"
PUBLISH=""
for arg in "$@"; do
  [ "$arg" = "--full" ] && VARIANT="full"
  [ "$arg" = "--windows" ] && PLATFORM="win"
  [ "$arg" = "--publish" ] && PUBLISH=1
done

MAC_ZIP="dist-release/SRT-Generator-${VERSION}-mac.zip"
WIN_ZIP="dist-release/SRT-Generator-${VERSION}-win-lite.zip"
NOTES="release-notes/${VERSION}.md"

if [ -n "$PUBLISH" ]; then
  for f in "$MAC_ZIP" "$WIN_ZIP" dist-release/appcast.xml "$NOTES"; do
    [ -f "$f" ] || { echo "!! $f is missing — build it first"; exit 1; }
  done
  grep -q "<sparkle:version>${VERSION}</sparkle:version>" dist-release/appcast.xml \
    || { echo "!! dist-release/appcast.xml has no item for ${VERSION}"; exit 1; }
  if gh release view "v${VERSION}" -R "$REPO" >/dev/null 2>&1; then
    echo "==> Release v${VERSION} exists: replacing its assets"
    gh release upload "v${VERSION}" -R "$REPO" --clobber "$MAC_ZIP" "$WIN_ZIP" dist-release/appcast.xml
  else
    echo "==> Creating release v${VERSION}"
    gh release create "v${VERSION}" -R "$REPO" --verify-tag --latest \
      --title "SRT Generator ${VERSION}" -F "$NOTES" "$MAC_ZIP" "$WIN_ZIP" dist-release/appcast.xml
  fi
  echo "✓ https://github.com/${REPO}/releases/tag/v${VERSION}"
  exit 0
fi

NAME="SRT-Generator-${VERSION}-${PLATFORM}-${VARIANT}"
[ "$PLATFORM" = "mac" ] && [ "$VARIANT" = "lite" ] && NAME="SRT-Generator-${VERSION}-mac"
# Windows: short inner folder name — deep paths inside .venv/models can hit
# Windows' 260-character path limit if the user unzips somewhere nested.
FOLDER="$NAME"
[ "$PLATFORM" = "win" ] && FOLDER="SRT-Generator"
STAGE="dist-release/${FOLDER}"
ASSETS="release-assets"
SPARKLE="$ASSETS/sparkle-${SPARKLE_VERSION}"

echo "==> Building ${NAME}"

# 1. Fresh frontend build
echo "==> Building frontend"
(cd frontend && npm run build >/dev/null)

# 2. Fetch bundled tools (cached across builds)
mkdir -p "$ASSETS"
if [ "$PLATFORM" = "mac" ]; then
  if [ ! -x "$ASSETS/uv" ]; then
    echo "==> Downloading uv (mac arm64)"
    curl -sL "https://github.com/astral-sh/uv/releases/latest/download/uv-aarch64-apple-darwin.tar.gz" \
      | tar -xz -C "$ASSETS" --strip-components 1 "uv-aarch64-apple-darwin/uv"
  fi
  for tool in ffmpeg ffprobe; do
    if [ ! -x "$ASSETS/$tool" ]; then
      echo "==> Downloading $tool (mac arm64, static)"
      curl -sL -o "$ASSETS/$tool.zip" \
        "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/$tool.zip"
      unzip -oq "$ASSETS/$tool.zip" "$tool" -d "$ASSETS"
      rm "$ASSETS/$tool.zip"
    fi
  done
  chmod +x "$ASSETS/uv" "$ASSETS/ffmpeg" "$ASSETS/ffprobe"
  if [ ! -d "$SPARKLE/Sparkle.framework" ]; then
    echo "==> Downloading Sparkle ${SPARKLE_VERSION}"
    mkdir -p "$SPARKLE"
    curl -sL "https://github.com/sparkle-project/Sparkle/releases/download/${SPARKLE_VERSION}/Sparkle-${SPARKLE_VERSION}.tar.xz" \
      | tar -xJ -C "$SPARKLE"
  fi
else
  if [ ! -f "$ASSETS/uv.exe" ]; then
    echo "==> Downloading uv (windows x64)"
    curl -sL -o "$ASSETS/uv-win.zip" \
      "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
    unzip -joq "$ASSETS/uv-win.zip" "*uv.exe" -d "$ASSETS" || unzip -joq "$ASSETS/uv-win.zip" "uv.exe" -d "$ASSETS"
    rm "$ASSETS/uv-win.zip"
  fi
  if [ ! -f "$ASSETS/ffmpeg.exe" ] || [ ! -f "$ASSETS/ffprobe.exe" ]; then
    echo "==> Downloading ffmpeg (windows x64, gyan.dev essentials)"
    curl -sL -o "$ASSETS/ffmpeg-win.zip" \
      "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    unzip -joq "$ASSETS/ffmpeg-win.zip" "*/bin/ffmpeg.exe" "*/bin/ffprobe.exe" -d "$ASSETS"
    rm "$ASSETS/ffmpeg-win.zip"
  fi
fi

# 3. Stage the app
echo "==> Staging files"
rm -rf "$STAGE"

if [ "$PLATFORM" = "mac" ]; then
  # Read-only .app bundle. All writable state (Python, venv, models, jobs/) goes to
  # ~/Library/Application Support/SRT Generator/ on first launch (see macapp/launcher).
  APP="$STAGE/SRT Generator.app"
  RES="$APP/Contents/Resources"
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Frameworks" "$RES/bin" "$RES/app/frontend"
  sed "s/__VERSION__/${VERSION}/g" macapp/Info.plist > "$APP/Contents/Info.plist"
  printf 'APPL????' > "$APP/Contents/PkgInfo"
  cp macapp/launcher "$APP/Contents/MacOS/launcher"
  cp macapp/launcher.py macapp/updater.py macapp/progress.html macapp/AppIcon.icns "$RES/"
  # Sparkle (auto-updates). The app isn't sandboxed, so its XPC services aren't needed.
  ditto "$SPARKLE/Sparkle.framework" "$APP/Contents/Frameworks/Sparkle.framework"
  rm -rf "$APP/Contents/Frameworks/Sparkle.framework/Versions/B/XPCServices" \
         "$APP/Contents/Frameworks/Sparkle.framework/XPCServices"
  cp "$ASSETS/uv" "$ASSETS/ffmpeg" "$ASSETS/ffprobe" "$RES/bin/"
  cp app.py pipeline.py transliterate.py corrections.py hinglish.py hinglish_model.py ai_polish.py review.py captions.py final_check.py config.py warmup.py VERSION requirements.txt README.md DECISIONS.md "$RES/app/"
  cp -R sample "$RES/app/sample"
  cp -R frontend/dist "$RES/app/frontend/dist"
  chmod +x "$APP/Contents/MacOS/launcher" "$RES"/bin/*
  if [ "$VARIANT" = "full" ]; then
    echo "==> Baking models into the app (full variant)"
    if [ ! -d models ]; then
      echo "!! models/ not found — run warmup.py once first"; exit 1
    fi
    cp -R models "$RES/models"
  fi
  # Ad-hoc signature on every build, inside out (Sparkle's helpers, the framework, then the
  # app), so Sparkle can install updates and relaunch without Gatekeeper prompts after the
  # first install. The bundle is never modified at runtime, so the signature stays valid.
  echo "==> Signing (ad-hoc)"
  FW="$APP/Contents/Frameworks/Sparkle.framework"
  codesign --force --sign - --timestamp=none -o runtime "$FW/Versions/B/Autoupdate"
  codesign --force --sign - --timestamp=none -o runtime "$FW/Versions/B/Updater.app"
  codesign --force --sign - --timestamp=none "$FW"
  codesign --force --sign - --timestamp=none "$APP"
  codesign --verify --deep --strict "$APP"
cat > "$STAGE/READ ME FIRST.txt" <<'EOF'
SRT GENERATOR — HOW TO INSTALL (Apple Silicon Macs: M1, M2, M3, M4)

1. Drag "SRT Generator" into your Applications folder.
   Had "Song SRT Generator" (version 3)? Your models, jobs and settings are
   moved over on the first launch, and the old app goes to the Trash.

2. Double-click it. macOS says "Apple could not verify…" — that's its
   warning for any app not from the App Store. Click Done (NOT Move to Bin).
   Then open System Settings > Privacy & Security, scroll down, click
   "Open Anyway" next to SRT Generator, and confirm.
   You only do this once.

3. The first launch sets everything up (5–20 minutes, needs internet once:
   about 2.5 GB of Python packages and AI models). Leave the window open
   until the app appears.

Updates: the app checks once a day and offers new versions itself (or use
SRT Generator > Check for Updates…). Your models are kept, so an update
takes about a minute.

Uninstall: delete the app and the folder
~/Library/Application Support/SRT Generator
If the setup window shows an error, click "Show the log" and send Aman the file.
EOF
else
  mkdir -p "$STAGE/bin" "$STAGE/frontend"
  cp app.py pipeline.py transliterate.py corrections.py hinglish.py hinglish_model.py ai_polish.py review.py captions.py final_check.py config.py warmup.py VERSION README.md DECISIONS.md "$STAGE/"
  cp -R sample "$STAGE/sample"
  cp -R frontend/dist "$STAGE/frontend/dist"
  cp requirements-windows.txt "$STAGE/"
  cp "$ASSETS/uv.exe" "$ASSETS/ffmpeg.exe" "$ASSETS/ffprobe.exe" "$STAGE/bin/"
  cp install.bat "$STAGE/Install.bat"
  cp start.bat "$STAGE/Start.bat"
  # Guard: .bat files MUST be CRLF + ASCII or cmd.exe breaks silently.
  for bat in "$STAGE/Install.bat" "$STAGE/Start.bat"; do
    if ! file "$bat" | grep -q "CRLF"; then
      echo "!! $bat is not CRLF — refusing to ship a broken batch file"; exit 1
    fi
    if ! file "$bat" | grep -q "ASCII"; then
      echo "!! $bat contains non-ASCII characters — refusing to ship"; exit 1
    fi
  done
  # Windows uses the cross-platform CPU separation engine.
  sed -i '' 's/^SEPARATOR_ENGINE = .*/SEPARATOR_ENGINE = "cpu"/' "$STAGE/config.py"
cat > "$STAGE/READ ME FIRST.txt" <<'EOF'
SRT GENERATOR - HOW TO INSTALL (Windows, 64-bit)

STEP 0 - put this whole folder somewhere simple first:
   your Desktop or Documents. (Not buried inside many nested folders -
   Windows has a path-length limit that can break the install.)

STEP 1 - install (one time):
   Double-click "Install.bat".
   If Windows shows a blue "Windows protected your PC" box:
   click "More info", then "Run anyway". (That's just Windows'
   warning for any app not from the Microsoft Store.)
   Wait for the window to say PASS.
   (First time takes 10-30 minutes - it downloads the AI models.
   Leave the window alone until PASS.)

STEP 2 - use it:
   Double-click "Start.bat". Your browser opens with the app.

That's it. From tomorrow on, only step 2.

NOTE: on Windows a 3-minute song takes roughly 2-5 minutes to process
(Macs have a special AI chip for this; Windows PCs do it the slow way).

Everything installs inside this folder only. To uninstall, delete the folder.
If the installer says anything other than PASS, send Aman a photo of the window.
EOF
# Windows text files need CRLF line endings.
perl -pi -e 's/\n/\r\n/' "$STAGE/READ ME FIRST.txt"
fi

# 4. Zip it
echo "==> Zipping"
if [ "$PLATFORM" = "mac" ]; then
  # The zip holds the .app itself (plus the read-me), not a wrapping folder. ditto keeps the
  # code signature (zip drops the extended attributes that hold the launcher script's).
  rm -f "dist-release/${NAME}.zip"
  ditto -c -k --sequesterRsrc "$STAGE" "dist-release/${NAME}.zip"
  CHECK="$(mktemp -d)"
  ditto -x -k "dist-release/${NAME}.zip" "$CHECK"
  codesign --verify --deep --strict "$CHECK/SRT Generator.app" \
    || { echo "!! the zipped app's signature doesn't verify"; rm -rf "$CHECK"; exit 1; }
  rm -rf "$CHECK"
else
  (cd dist-release && rm -f "${NAME}.zip" && zip -rq "${NAME}.zip" "$FOLDER")
fi
SIZE=$(du -h "dist-release/${NAME}.zip" | cut -f1)
echo ""
echo "✓ dist-release/${NAME}.zip  (${SIZE})"
if [ "$PLATFORM" = "mac" ] && [ "$VARIANT" = "lite" ]; then
  # 5. Sparkle: sign the zip with the Keychain's EdDSA key and write the appcast.
  [ -f "$NOTES" ] || { echo "!! $NOTES is missing — write the release notes first"; exit 1; }
  echo "==> Signing the zip for Sparkle (EdDSA key from the Keychain)"
  SIGNATURE="$("$SPARKLE/bin/sign_update" --account "$SPARKLE_ACCOUNT" -p "dist-release/${NAME}.zip")"
  python3 macapp/make_appcast.py "$VERSION" "dist-release/${NAME}.zip" "$SIGNATURE" "$NOTES" \
    dist-release/appcast.xml ${APPCAST_ARGS:-}
fi
if [ "$PLATFORM" = "mac" ]; then
  echo "  Teammate steps: unzip → drag to Applications → open → Open Anyway (Privacy & Security) → wait for setup"
else
  echo "  Teammate steps: unzip → Install.bat (Run anyway) → Start.bat"
fi
