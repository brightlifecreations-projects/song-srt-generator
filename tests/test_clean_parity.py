"""V4.8: the Review panel's cleanCaption() (frontend/src/reviewFix.js) and captions.clean_caption()
give the same text, so what the panel shows is what the SRT gets. Needs Node (skipped without)."""

import json
import random
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import captions  # noqa: E402

POOL = list("aZé ñ 07%:.'’\"“”‘-–—/\\|_~,;!?…()[]{}*#@&♪😀👍🏽‍‌️¿¡²।") + \
    ["मैं", "हूँ", "don't", "7:30", "70%", "...", " - ", "x"]


class Parity(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node isn't installed")
    def test_same_as_the_panel(self):
        rng = random.Random(48)
        samples = ["".join(rng.choice(POOL) for _ in range(rng.randint(1, 14))) for _ in range(800)]
        script = ("import(process.argv[1]).then(m => { const xs = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
                  " process.stdout.write(JSON.stringify(xs.map(m.cleanCaption))); })")
        out = subprocess.run(["node", "-e", script, (ROOT / "frontend/src/reviewFix.js").as_uri()],
                             input=json.dumps(samples), capture_output=True, text=True, check=True)
        js = json.loads(out.stdout)
        for text, theirs in zip(samples, js):
            self.assertEqual(captions.clean_caption(text), theirs, repr(text))


if __name__ == "__main__":
    unittest.main()
