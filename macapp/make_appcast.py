"""Write or update the Sparkle appcast for a Mac release (called by make_release.sh).

    python3 macapp/make_appcast.py <version> <mac.zip> <ed-signature> <notes.md> <out.xml>
        [--download-base URL] [--merge-from URL|none]

The item for <version> replaces any older item with the same version; the newest five
releases are kept. --merge-from (default: the published feed) is read first so earlier
items carry over; "none" starts a fresh feed. Release notes are the same Markdown file
as the GitHub release, turned into simple HTML for Sparkle's update window.
"""

import argparse
import html
import os
import re
import urllib.request
from email.utils import formatdate
from pathlib import Path

REPO = "brightlifecreations-projects/song-srt-generator"
FEED_URL = f"https://github.com/{REPO}/releases/latest/download/appcast.xml"
KEEP = 5


def inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return re.sub(r"\[(.+?)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', text)


def markdown_to_html(md: str) -> str:
    """Headings, bullet lists and paragraphs — all the release notes use."""
    out, in_list, para = [], False, []

    def flush():
        nonlocal para
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para = []

    for line in md.splitlines():
        stripped = line.strip()
        bullet = re.match(r"^[-*] (.*)", stripped)
        if bullet:
            flush()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(bullet.group(1))}</li>")
            continue
        if in_list and stripped:
            if line.startswith("  "):  # continuation of the previous bullet
                out[-1] = out[-1][:-5] + " " + inline(stripped) + "</li>"
                continue
        if in_list:
            out.append("</ul>")
            in_list = False
        heading = re.match(r"^(#{1,6}) (.*)", stripped)
        if heading:
            flush()
            level = min(len(heading.group(1)) + 1, 4)
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
        elif stripped:
            para.append(stripped)
        else:
            flush()
    flush()
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def existing_items(source: str) -> list[str]:
    if source == "none":
        return []
    try:
        if re.match(r"https?://", source):
            with urllib.request.urlopen(source, timeout=10) as r:
                text = r.read().decode("utf-8")
        else:
            text = Path(source).read_text(encoding="utf-8")
    except Exception:
        return []  # no feed published yet (or offline): start fresh
    return re.findall(r"<item>.*?</item>", text, flags=re.S)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("zip")
    ap.add_argument("signature")
    ap.add_argument("notes")
    ap.add_argument("out")
    ap.add_argument("--download-base", default=None)
    ap.add_argument("--merge-from", default=FEED_URL)
    args = ap.parse_args()

    version = args.version
    zip_path = Path(args.zip)
    base = args.download_base or f"https://github.com/{REPO}/releases/download/v{version}"
    notes = markdown_to_html(Path(args.notes).read_text(encoding="utf-8"))
    item = f"""<item>
      <title>Version {version}</title>
      <pubDate>{formatdate(usegmt=True)}</pubDate>
      <sparkle:version>{version}</sparkle:version>
      <sparkle:shortVersionString>{version}</sparkle:shortVersionString>
      <sparkle:minimumSystemVersion>12.0</sparkle:minimumSystemVersion>
      <description><![CDATA[
{notes}
      ]]></description>
      <enclosure url="{base}/{zip_path.name}" length="{os.path.getsize(zip_path)}" type="application/octet-stream" sparkle:edSignature="{args.signature}"/>
    </item>"""

    others = [i for i in existing_items(args.merge_from)
              if f"<sparkle:version>{version}</sparkle:version>" not in i]
    items = [item] + others[:KEEP - 1]
    feed = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>SRT Generator</title>
    <link>{FEED_URL}</link>
    <description>Updates for SRT Generator (Mac).</description>
    <language>en</language>
    {chr(10).join("    " + i if n else i for n, i in enumerate(items))}
  </channel>
</rss>
"""
    Path(args.out).write_text(feed, encoding="utf-8")
    print(f"✓ {args.out}  ({len(items)} item{'s' if len(items) != 1 else ''}, newest {version})")


if __name__ == "__main__":
    main()
