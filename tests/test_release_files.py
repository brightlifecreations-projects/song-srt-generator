"""Every Python module of the app is shipped (V4.8: listen_check.py was new). The release scripts copy
an explicit list of files; a module left out would make the installed app fail on import."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ReleaseFiles(unittest.TestCase):
    def test_every_module_is_shipped(self):
        modules = {p.name for p in ROOT.glob("*.py")}
        mac = (ROOT / "make_release.sh").read_text(encoding="utf-8")
        lines = [l for l in mac.splitlines() if l.strip().startswith("cp app.py")]
        self.assertEqual(len(lines), 2)  # the Mac app and the Windows zip
        for line in lines:
            self.assertEqual(modules - set(line.split()), set(), line)
        win = (ROOT / "make_release_windows.ps1").read_text(encoding="utf-8")
        self.assertEqual(modules - set(re.findall(r'"([\w]+\.py)"', win)), set())


if __name__ == "__main__":
    unittest.main()
