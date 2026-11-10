<#
Build (and optionally publish) the Windows release of SRT Generator. DECISIONS.md V4.7.

    .\make_release_windows.ps1 4.7               -> dist-release\SRT-Generator-4.7-Setup.exe
                                                     dist-release\SRT-Generator-4.7-win-lite.zip
                                                     dist-release\appcast-windows.xml (signed item)
    .\make_release_windows.ps1 4.7 -Publish      the above, then GitHub release v4.7 (created, or added to)

Options for tests: -DownloadBase <url> (where the appcast points for the Setup.exe, e.g. a local
server) and -MergeFrom <url|file|none> (the feed whose older items are kept; default: published).

The version must match the VERSION file and release-notes\<version>.md must exist. The Setup.exe is
signed for WinSparkle with the EdDSA key in this PC's Windows Credential Manager
("SRT Generator release key (Windows)"); it is never written to a file here.
Downloads (uv, ffmpeg, WinSparkle, Inno Setup) are cached in release-assets\.
#>
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Version,
    [switch]$Publish,
    [string]$DownloadBase = "",
    [string]$MergeFrom = ""
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Repo = "brightlifecreations-projects/song-srt-generator"
$WinSparkleVersion = "0.9.4"
$InnoVersion = "6.7.4"
$Assets = Join-Path $PSScriptRoot "release-assets"
$Dist = Join-Path $PSScriptRoot "dist-release"
$Notes = Join-Path $PSScriptRoot "release-notes\$Version.md"
$SetupName = "SRT-Generator-$Version-Setup.exe"
$ZipName = "SRT-Generator-$Version-win-lite.zip"
$Setup = Join-Path $Dist $SetupName
$Zip = Join-Path $Dist $ZipName
$Appcast = Join-Path $Dist "appcast-windows.xml"
$AppFiles = @("app.py", "pipeline.py", "transliterate.py", "corrections.py", "hinglish.py", "hinglish_model.py",
              "ai_polish.py", "review.py", "captions.py", "final_check.py", "config.py", "warmup.py", "VERSION",
              "README.md", "DECISIONS.md", "requirements-windows.txt")

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "!! $msg" -ForegroundColor Red; exit 1 }
function Run($exe) {
    & $exe @args
    if ($LASTEXITCODE -ne 0) { Fail "$exe $args failed (exit $LASTEXITCODE)" }
}

$FileVersion = (Get-Content (Join-Path $PSScriptRoot "VERSION") -Raw).Trim()
if ($FileVersion -ne $Version) { Fail "VERSION file says $FileVersion, not $Version. Edit VERSION first (it's the single source)." }
if (-not (Test-Path $Notes)) { Fail "$Notes is missing - write the release notes first" }
New-Item -ItemType Directory -Force $Assets, $Dist | Out-Null

# ---------------------------------------------------------------- 1. Tools (cached)
Step "Tools"
$Dl = Join-Path $Assets "dl"
New-Item -ItemType Directory -Force $Dl | Out-Null
if (-not (Test-Path "$Assets\uv.exe")) {
    Step "Downloading uv (windows x64)"
    Invoke-WebRequest "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip" -OutFile "$Dl\uv-win.zip" -UseBasicParsing
    Expand-Archive "$Dl\uv-win.zip" "$Dl\uv" -Force
    Copy-Item (Get-ChildItem "$Dl\uv" -Recurse -Filter uv.exe | Select-Object -First 1).FullName "$Assets\uv.exe"
}
if (-not (Test-Path "$Assets\ffmpeg.exe") -or -not (Test-Path "$Assets\ffprobe.exe")) {
    Step "Downloading ffmpeg (gyan.dev essentials)"
    Invoke-WebRequest "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile "$Dl\ffmpeg-win.zip" -UseBasicParsing
    Expand-Archive "$Dl\ffmpeg-win.zip" "$Dl\ffmpeg" -Force
    foreach ($t in "ffmpeg.exe", "ffprobe.exe") {
        Copy-Item (Get-ChildItem "$Dl\ffmpeg" -Recurse -Filter $t | Select-Object -First 1).FullName "$Assets\$t"
    }
}
$WS = Join-Path $Assets "winsparkle\WinSparkle-$WinSparkleVersion"
if (-not (Test-Path "$WS\x64\Release\WinSparkle.dll")) {
    Step "Downloading WinSparkle $WinSparkleVersion"
    Run gh release download "v$WinSparkleVersion" -R vslavik/winsparkle -p "WinSparkle-$WinSparkleVersion.zip" -D $Dl --clobber
    Expand-Archive "$Dl\WinSparkle-$WinSparkleVersion.zip" (Join-Path $Assets "winsparkle") -Force
}
$Iscc = Join-Path $Assets "innosetup\ISCC.exe"
if (-not (Test-Path $Iscc)) {
    Step "Installing Inno Setup $InnoVersion into release-assets (per user)"
    $tag = "is-" + $InnoVersion.Replace(".", "_")
    Run gh release download $tag -R jrsoftware/issrc -p "innosetup-$InnoVersion.exe" -D $Dl --clobber
    $p = Start-Process "$Dl\innosetup-$InnoVersion.exe" -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
        "/CURRENTUSER", "/NOICONS", "/DIR=`"$Assets\innosetup`"" -Wait -PassThru
    if ($p.ExitCode -ne 0) { Fail "Inno Setup didn't install" }
}
$ToolsPy = Join-Path $Assets "tools-venv\Scripts\python.exe"
if (-not (Test-Path $ToolsPy)) {
    Step "Release tools venv (cryptography, keyring)"
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $Assets "python"
    Run "$Assets\uv.exe" venv --python 3.10 (Join-Path $Assets "tools-venv")
    Run "$Assets\uv.exe" pip install --python $ToolsPy cryptography keyring
}
$Public = (& $ToolsPy winapp\release_key.py public).Trim()
if ($LASTEXITCODE -ne 0) { Fail "No Windows release key in Credential Manager (see winapp\release_key.py)" }
if (-not (Select-String -Path winapp\winupdater.py -SimpleMatch "EDDSA_PUBLIC_KEY = `"$Public`"" -Quiet)) {
    Fail "winapp\winupdater.py has a different public key than the key in Credential Manager"
}

# ---------------------------------------------------------------- 2. Frontend
Step "Building the frontend"
Push-Location frontend
if (-not (Test-Path node_modules)) { Run npm.cmd install --no-audit --no-fund }
Run npm.cmd run build
Pop-Location

# ---------------------------------------------------------------- 3. The app folder (installer)
Step "Staging the app"
$Stage = Join-Path $Dist "win-app"
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Force "$Stage\bin", "$Stage\winapp", "$Stage\frontend" | Out-Null
foreach ($f in $AppFiles) { Copy-Item $f "$Stage\$f" }
Copy-Item -Recurse sample "$Stage\sample"
Copy-Item -Recurse frontend\dist "$Stage\frontend\dist"
foreach ($t in "uv.exe", "ffmpeg.exe", "ffprobe.exe") { Copy-Item "$Assets\$t" "$Stage\bin\$t" }
Copy-Item "$WS\x64\Release\WinSparkle.dll" "$Stage\WinSparkle.dll"
Copy-Item "$WS\COPYING" "$Stage\COPYING.WinSparkle.txt"
foreach ($f in "launcher.py", "winupdater.py", "progress.html", "app.ico") { Copy-Item "winapp\$f" "$Stage\winapp\$f" }
Copy-Item LICENSE "$Stage\LICENSE.txt"
Step "Wheels for the source-only packages"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Assets "python"
Run $ToolsPy winapp\build_wheels.py "$Assets\uv.exe" (Join-Path $Assets "wheels")
New-Item -ItemType Directory -Force "$Stage\wheels" | Out-Null
Copy-Item (Join-Path $Assets "wheels\*.whl") "$Stage\wheels\"
$Csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
Run $Csc "-nologo" "-target:winexe" "-optimize" "-win32icon:winapp\app.ico" "-out:$Stage\SRT Generator.exe" `
    "-r:System.Windows.Forms.dll" "-r:System.Drawing.dll" winapp\launcher.cs

Step "Building the installer (Inno Setup)"
Run $Iscc /Q "/DVersion=$Version" "/DStage=$Stage" "/DOut=$Dist" winapp\installer.iss
if (-not (Test-Path $Setup)) { Fail "$Setup wasn't built" }

# ---------------------------------------------------------------- 4. The win-lite zip (Install.bat / Start.bat)
Step "Building the win-lite zip"
$ZipStage = Join-Path $Dist "zip-stage"
if (Test-Path $ZipStage) { Remove-Item -Recurse -Force $ZipStage }
$Folder = Join-Path $ZipStage "SRT-Generator"
New-Item -ItemType Directory -Force "$Folder\bin", "$Folder\frontend" | Out-Null
foreach ($f in $AppFiles) { Copy-Item $f "$Folder\$f" }
Copy-Item -Recurse sample "$Folder\sample"
Copy-Item -Recurse frontend\dist "$Folder\frontend\dist"
foreach ($t in "uv.exe", "ffmpeg.exe", "ffprobe.exe") { Copy-Item "$Assets\$t" "$Folder\bin\$t" }
Copy-Item install.bat "$Folder\Install.bat"
Copy-Item start.bat "$Folder\Start.bat"
foreach ($bat in "$Folder\Install.bat", "$Folder\Start.bat") {
    $bytes = [IO.File]::ReadAllBytes($bat)
    $text = [Text.Encoding]::ASCII.GetString($bytes)
    if ($bytes | Where-Object { $_ -gt 127 }) { Fail "$bat contains non-ASCII characters - refusing to ship" }
    if (($text -split "`r`n").Count -ne ($text -split "`n").Count) { Fail "$bat is not CRLF - refusing to ship" }
}
$cfg = Get-Content "$Folder\config.py" -Raw
[IO.File]::WriteAllText("$Folder\config.py", ($cfg -replace '(?m)^SEPARATOR_ENGINE = .*$', 'SEPARATOR_ENGINE = "cpu"'), (New-Object Text.UTF8Encoding $false))
$readme = @"
SRT GENERATOR - WIN-LITE ZIP (Windows, 64-bit)

Most people should use SRT-Generator-$Version-Setup.exe instead: a normal Windows app with its
own window, Start Menu shortcut and automatic updates. This zip is the older way, which runs in
your browser and doesn't update itself.

STEP 0 - put this whole folder somewhere short, like C:\SRT-Generator.
STEP 1 - double-click Install.bat (one time). If Windows shows "Windows protected your PC":
         click "More info", then "Run anyway". Wait for PASS (10-30 minutes the first time).
STEP 2 - double-click Start.bat. Your browser opens with the app.

Everything installs inside this folder only. To uninstall, delete the folder.
"@
[IO.File]::WriteAllText("$Folder\READ ME FIRST.txt", ($readme -replace "`r?`n", "`r`n"), [Text.Encoding]::ASCII)
if (Test-Path $Zip) { Remove-Item $Zip }
Compress-Archive -Path $Folder -DestinationPath $Zip -CompressionLevel Optimal

# ---------------------------------------------------------------- 5. Sign and write the appcast
Step "Signing the installer for WinSparkle (key from Credential Manager)"
$Signature = (& $ToolsPy winapp\release_key.py sign $Setup).Trim()
if ($LASTEXITCODE -ne 0 -or -not $Signature) { Fail "Signing failed" }
Run "$WS\bin\winsparkle-tool.exe" verify --public-key $Public --signature $Signature $Setup

Step "Writing appcast-windows.xml"
$appcastArgs = @("winapp\make_appcast_windows.py", $Version, $Setup, $Signature, $Notes, $Appcast)
if ($DownloadBase) { $appcastArgs += @("--download-base", $DownloadBase) }
if ($MergeFrom) { $appcastArgs += @("--merge-from", $MergeFrom) }
Run $ToolsPy @appcastArgs

$size = "{0:N1} MB" -f ((Get-Item $Setup).Length / 1MB)
Write-Host ""
Write-Host "OK  $Setup  ($size)"
Write-Host "OK  $Zip"
Write-Host "OK  $Appcast"
if (-not $Publish) { exit 0 }

# ---------------------------------------------------------------- 6. Publish to GitHub
Step "Publishing v$Version"
$tag = "v$Version"
# Through cmd: Windows PowerShell turns a native command's stderr ("release not found") into an
# error when $ErrorActionPreference is Stop.
cmd /c "gh release view $tag -R $Repo >nul 2>nul"
if ($LASTEXITCODE -eq 0) {
    Step "Release $tag exists: adding the Windows assets"
    Run gh release upload $tag -R $Repo --clobber $Setup $Zip $Appcast
} else {
    Step "Creating release $tag"
    Run gh release create $tag -R $Repo --verify-tag --latest --title "SRT Generator $Version" -F $Notes $Setup $Zip $Appcast
}
# The Mac feed is .../releases/latest/download/appcast.xml. When this release has no Mac build,
# attach the appcast.xml of the newest release that has one, unchanged, so the Mac keeps working.
$assets = gh release view $tag -R $Repo --json assets --jq ".assets[].name"
if (-not ($assets | Where-Object { $_ -like "*-mac.zip" })) {
    $source = $null
    foreach ($t in (gh release list -R $Repo -L 30 --json tagName --jq ".[].tagName")) {
        if ($t -eq $tag) { continue }
        $names = gh release view $t -R $Repo --json assets --jq ".assets[].name"
        if (($names -contains "appcast.xml") -and ($names | Where-Object { $_ -like "*-mac.zip" })) { $source = $t; break }
    }
    if (-not $source) { Fail "No earlier release has a Mac build and appcast.xml" }
    Step "No Mac build in ${tag}: attaching appcast.xml from $source unchanged"
    $macDir = Join-Path $Dist "mac-appcast"
    New-Item -ItemType Directory -Force $macDir | Out-Null
    Run gh release download $source -R $Repo -p appcast.xml -D $macDir --clobber
    Run gh release upload $tag -R $Repo --clobber (Join-Path $macDir "appcast.xml")
}
Write-Host "OK  https://github.com/$Repo/releases/tag/$tag"
