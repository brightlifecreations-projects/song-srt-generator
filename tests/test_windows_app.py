"""The Windows app's pieces that can be tested without a window (V4.7): the appcast (five newest
versions, installer arguments, signature, notes), moving an old zip install's models and jobs, and
the release key's sign/verify round trip (skipped without the release tools' cryptography).

    .venv\\Scripts\\python -m unittest tests.test_windows_app -v
"""

import base64
import importlib.util
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


appcast = load(ROOT / "winapp" / "make_appcast_windows.py", "make_appcast_windows")


class Appcast(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.setup = self.tmp / "SRT-Generator-4.7-Setup.exe"
        self.setup.write_bytes(b"x" * 1234)

    def test_item(self):
        feed, count = appcast.build("4.7", self.setup, "SIG==", "## Notes\n\n- **New** thing", "none")
        self.assertEqual(count, 1)
        self.assertIn("<sparkle:version>4.7</sparkle:version>", feed)
        self.assertIn('url="https://github.com/brightlifecreations-projects/song-srt-generator/releases/download/v4.7/'
                      'SRT-Generator-4.7-Setup.exe"', feed)
        self.assertIn('sparkle:installerArguments="/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-"', feed)
        self.assertIn('sparkle:os="windows"', feed)
        self.assertIn('length="1234"', feed)
        self.assertIn('sparkle:edSignature="SIG=="', feed)
        self.assertIn("<li><b>New</b> thing</li>", feed)
        self.assertIn("background: #ffffff", feed)  # readable in WinSparkle's window in dark mode

    def test_keeps_five_newest_and_replaces_same_version(self):
        old = self.tmp / "old.xml"
        items = "".join(f"<item><title>Version 4.{n}</title><sparkle:version>4.{n}</sparkle:version></item>"
                        for n in (7, 6, 5, 4, 3, 2))
        old.write_text(f"<rss><channel>{items}</channel></rss>", encoding="utf-8")
        feed, count = appcast.build("4.7", self.setup, "S", "notes", str(old))
        self.assertEqual(count, 5)
        versions = re.findall(r"<sparkle:version>(.*?)</sparkle:version>", feed)
        self.assertEqual(versions, ["4.7", "4.6", "4.5", "4.4", "4.3"])

    def test_test_feed_download_base(self):
        feed, _ = appcast.build("4.7.1", self.setup, "S", "n", "none", "http://127.0.0.1:8899")
        self.assertIn('url="http://127.0.0.1:8899/SRT-Generator-4.7-Setup.exe"', feed)


@unittest.skipUnless(sys.platform == "win32", "the Windows launcher")
class Migration(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["SRT_APP_DIR"] = str(ROOT)
        os.environ["SRT_DATA_DIR"] = str(self.tmp / "data")
        (self.tmp / "data").mkdir()
        cwd = os.getcwd()
        self.launcher = load(ROOT / "winapp" / "launcher.py", "win_launcher")
        os.chdir(cwd)

    def test_move_tree_moves_jobs_and_models_and_keeps_existing(self):
        old = self.tmp / "old"
        (old / "jobs" / "20261101-song").mkdir(parents=True)
        (old / "jobs" / "20261101-song" / "song.srt").write_text("1", encoding="utf-8")
        (old / "models" / "hf" / "hub").mkdir(parents=True)
        (old / "models" / "hf" / "hub" / "weights.bin").write_bytes(b"w")
        new = self.tmp / "data"
        (new / "jobs" / "20261101-song").mkdir(parents=True)          # same job already here: kept
        (new / "jobs" / "20261101-song" / "song.srt").write_text("mine", encoding="utf-8")
        moved_jobs = self.launcher.move_tree(old / "jobs", new / "jobs")
        moved_models = self.launcher.move_tree(old / "models", new / "models")
        self.assertEqual((new / "jobs" / "20261101-song" / "song.srt").read_text(encoding="utf-8"), "mine")
        self.assertTrue((old / "jobs" / "20261101-song" / "song.srt").exists())  # not overwritten, left in place
        self.assertEqual(moved_jobs, 0)
        self.assertEqual(moved_models, 1)
        self.assertTrue((new / "models" / "hf" / "hub" / "weights.bin").exists())
        self.assertFalse((old / "models").exists())

    def test_old_installs_found(self):
        old = self.tmp / "SRT-Generator"
        (old / "models").mkdir(parents=True)
        (old / "app.py").write_text("", encoding="utf-8")
        self.launcher.OLD_INSTALL_PLACES[:] = [old, self.tmp / "nothing-here"]
        self.assertEqual(self.launcher.old_installs(), [old])


class ReleaseKey(unittest.TestCase):
    def test_sign_and_verify_round_trip(self):
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        except ImportError:
            self.skipTest("cryptography isn't installed (it's in the release tools venv)")
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        key = Ed25519PrivateKey.generate()
        data = b"installer bytes"
        signature = key.sign(data)
        public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        key.public_key().verify(signature, data)
        self.assertEqual(len(base64.b64encode(public).decode()), 44)  # the format WinSparkle takes
        with self.assertRaises(Exception):
            key.public_key().verify(signature, data + b"!")

    def test_public_key_in_the_app_is_valid_base64_ed25519(self):
        text = (ROOT / "winapp" / "winupdater.py").read_text(encoding="utf-8")
        key = re.search(r'^EDDSA_PUBLIC_KEY = "([^"]+)"', text, re.M).group(1)
        self.assertEqual(len(base64.b64decode(key)), 32)


if __name__ == "__main__":
    unittest.main()
