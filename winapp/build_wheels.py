"""Wheels for the pinned packages that PyPI only has as source (DECISIONS.md V4.7).

    python winapp/build_wheels.py <uv.exe> <out-dir>

openai-whisper, stable-ts and proxy-tools publish no wheels, so every install would build them
from source, in deep folders under the data folder's uv cache, where Windows' 260-character path
limit can break the build (seen in testing). make_release_windows.ps1 builds them once here and
ships them in the installer's wheels\\ folder; the app installs with --find-links to it.
"""

import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_ONLY = ("openai-whisper", "stable-ts", "proxy-tools")


def main(uv: str, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    pins = dict(line.strip().split("==") for line in (ROOT / "requirements-windows.txt").read_text().splitlines()
                if "==" in line and not line.startswith("#"))
    for name in SOURCE_ONLY:
        version = pins[name]
        norm = name.replace("-", "_")
        if list(out.glob(f"{norm}-{version}-*.whl")):
            print(f"have {name} {version}")
            continue
        info = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30))
        sdist = next(u for u in info["urls"] if u["packagetype"] == "sdist")
        with tempfile.TemporaryDirectory(prefix="w-", dir=str(out.anchor)) as tmp:  # a short path
            archive = Path(tmp) / sdist["filename"]
            urllib.request.urlretrieve(sdist["url"], archive)
            subprocess.run([uv, "build", "--wheel", "--python", "3.10", "-o", str(out), str(archive)], check=True)
        print(f"built {name} {version}")


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
