"""Write or update the WinSparkle appcast for a Windows release (called by make_release_windows.ps1).

    python winapp/make_appcast_windows.py <version> <Setup.exe> <ed-signature> <notes.md> <out.xml>
        [--download-base URL] [--merge-from URL|file|none]

Same rules as the Mac's macapp/make_appcast.py (DECISIONS V4.0): the item for <version> replaces an
older item with the same version, the newest five releases are kept, and --merge-from (default:
the published appcast-windows.xml) is read first so earlier items carry over. WinSparkle runs the
installer with sparkle:installerArguments: silent, then the installer reopens the app.
"""

import argparse
import importlib.util
import os
from email.utils import formatdate
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("make_appcast", ROOT / "macapp" / "make_appcast.py")
mac = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mac)

REPO = mac.REPO
FEED_URL = f"https://github.com/{REPO}/releases/latest/download/appcast-windows.xml"
KEEP = mac.KEEP
INSTALLER_ARGS = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-"


def build(version: str, setup: Path, signature: str, notes_md: str, merge_from: str,
          download_base: str | None = None) -> tuple[str, int]:
    base = download_base or f"https://github.com/{REPO}/releases/download/v{version}"
    # WinSparkle shows the notes in Windows' old HTML view, which follows dark mode with black
    # text on black unless the page sets its own colours (seen in testing).
    notes = ('<style>body { background: #ffffff; color: #1a1d26; font: 13px "Segoe UI", sans-serif; } '
             'h2, h3 { font-size: 15px; margin: 6px 0; } ul { padding-left: 18px; }</style>\n'
             + mac.markdown_to_html(notes_md))
    item = f"""<item>
      <title>Version {version}</title>
      <pubDate>{formatdate(usegmt=True)}</pubDate>
      <sparkle:version>{version}</sparkle:version>
      <sparkle:shortVersionString>{version}</sparkle:shortVersionString>
      <description><![CDATA[
{notes}
      ]]></description>
      <enclosure url="{base}/{setup.name}" sparkle:os="windows" sparkle:installerArguments="{INSTALLER_ARGS}" length="{os.path.getsize(setup)}" type="application/octet-stream" sparkle:edSignature="{signature}"/>
    </item>"""
    others = [i for i in mac.existing_items(merge_from) if f"<sparkle:version>{version}</sparkle:version>" not in i]
    items = [item] + others[:KEEP - 1]
    feed = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>SRT Generator</title>
    <link>{FEED_URL}</link>
    <description>Updates for SRT Generator (Windows).</description>
    <language>en</language>
    {chr(10).join("    " + i if n else i for n, i in enumerate(items))}
  </channel>
</rss>
"""
    return feed, len(items)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("setup")
    ap.add_argument("signature")
    ap.add_argument("notes")
    ap.add_argument("out")
    ap.add_argument("--download-base", default=None)
    ap.add_argument("--merge-from", default=FEED_URL)
    args = ap.parse_args()
    feed, count = build(args.version, Path(args.setup), args.signature,
                        Path(args.notes).read_text(encoding="utf-8"), args.merge_from, args.download_base)
    Path(args.out).write_bytes(feed.encode("utf-8"))
    print(f"wrote {args.out} ({count} item{'s' if count != 1 else ''}, newest {args.version})")


if __name__ == "__main__":
    main()
