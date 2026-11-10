"""The Windows release key (DECISIONS.md V4.7): an ed25519 (EdDSA) key pair for WinSparkle.

The private key lives only in the release laptop's Windows Credential Manager (generic credential
"SRT Generator release key (Windows)"), never in a file in this repo. Run with the release tools
venv (cryptography + keyring), which make_release_windows.ps1 creates:

    python winapp/release_key.py generate          once: make the key, store it, print the public key
    python winapp/release_key.py backup <file>     write the one backup copy (for a password manager)
    python winapp/release_key.py public            the public key (base64), as in winupdater.py
    python winapp/release_key.py sign <file>       the sparkle:edSignature for a file (base64)
    python winapp/release_key.py verify <file> <signature> [public]   exit 0 if it verifies

The private key is the 32-byte seed in base64, the format winsparkle-tool uses for its key files.
"""

import base64
import sys
from pathlib import Path

import keyring
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

SERVICE = "SRT Generator release key (Windows)"
ACCOUNT = "eddsa-ed25519"


def _private() -> Ed25519PrivateKey:
    seed = keyring.get_password(SERVICE, ACCOUNT)
    if not seed:
        sys.exit(f"No release key in Credential Manager ({SERVICE}). Run: release_key.py generate")
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(seed))


def _public_b64(key: Ed25519PrivateKey) -> str:
    return base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "generate":
        if keyring.get_password(SERVICE, ACCOUNT):
            sys.exit("A release key already exists in Credential Manager; not replacing it.")
        key = Ed25519PrivateKey.generate()
        from cryptography.hazmat.primitives.serialization import NoEncryption, PrivateFormat
        seed = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        keyring.set_password(SERVICE, ACCOUNT, base64.b64encode(seed).decode())
        print(_public_b64(key))
    elif cmd == "backup":
        out = Path(argv[1])
        if out.exists():
            sys.exit(f"{out} exists; not overwriting it.")
        key = _private()
        from cryptography.hazmat.primitives.serialization import NoEncryption, PrivateFormat
        seed = base64.b64encode(key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode()
        out.write_text(
            "SRT Generator — Windows release signing key (EdDSA / ed25519) — BACKUP\r\n"
            "Keep this secret. Move it into your password manager, then delete this file.\r\n\r\n"
            f"Private key (base64 seed; also a valid winsparkle-tool key file line):\r\n{seed}\r\n\r\n"
            f"Public key (in winapp/winupdater.py):\r\n{_public_b64(key)}\r\n\r\n"
            "To restore it on a release PC (with the release tools venv):\r\n"
            f"  python -c \"import keyring; keyring.set_password('{SERVICE}', '{ACCOUNT}', '<private key>')\"\r\n",
            encoding="utf-8")
        print(out)
    elif cmd == "public":
        print(_public_b64(_private()))
    elif cmd == "sign":
        print(base64.b64encode(_private().sign(Path(argv[1]).read_bytes())).decode())
    elif cmd == "verify":
        public = argv[3] if len(argv) > 3 else _public_b64(_private())
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(public)).verify(
                base64.b64decode(argv[2]), Path(argv[1]).read_bytes())
        except InvalidSignature:
            print("INVALID")
            return 1
        print("OK")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
