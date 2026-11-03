#!/bin/bash
# Build whisper-hinglish-apex-int8.zip, the Hinglish model SRT Generator downloads (DECISIONS.md V4.2).
#
#   ./build.sh <work-dir>
#
# Downloads Oriserve/Whisper-Hindi2Hinglish-Apex at a pinned revision, adds the base model's
# alignment heads (word timestamps need them), converts it to CTranslate2 int8 and zips it with
# the Apache-2.0 LICENSE and the NOTICE crediting Oriserve. Prints the size and SHA-256 that go
# into config.py (HINGLISH_MODEL_MB, HINGLISH_MODEL_SHA256). Needs internet and ~5 GB free;
# installs its converter (ctranslate2 + transformers + torch) into <work-dir>/venv, never the app's.
#
# Publish (not marked "latest", so Sparkle's feed and the update check keep pointing at the app):
#   gh release create model-hinglish-apex-int8-v1 --latest=false \
#     --title "Hinglish model (Apex, int8) — download used by SRT Generator 4.2+" \
#     --notes-file NOTICE <work-dir>/whisper-hinglish-apex-int8.zip
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="${1:?usage: ./build.sh <work-dir>}"
REPO="Oriserve/Whisper-Hindi2Hinglish-Apex"
REVISION="f3214eed20b4e4d4144e739982d911f87b9cb223"
UV="$HERE/../../bin/uv"
mkdir -p "$WORK/hf"
cd "$WORK"

echo "==> Downloading $REPO@$REVISION"
for f in added_tokens.json config.json generation_config.json merges.txt normalizer.json \
         preprocessor_config.json special_tokens_map.json tokenizer.json tokenizer_config.json \
         vocab.json model.safetensors; do
  [ -s "hf/$f" ] || curl -sfL -o "hf/$f" "https://huggingface.co/$REPO/resolve/$REVISION/$f"
done

echo "==> Adding the base model's alignment heads"
python3 - hf/generation_config.json <<'EOF'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["alignment_heads"] = [[2, 4], [2, 11], [3, 3], [3, 6], [3, 11], [3, 14]]  # whisper-large-v3-turbo
json.dump(d, open(p, "w"), indent=2)
EOF

echo "==> Converting to CTranslate2 int8"
[ -x venv/bin/python ] || "$UV" venv -q -p 3.10 venv
VIRTUAL_ENV="$WORK/venv" "$UV" pip install -q ctranslate2==4.8.1 "transformers[torch]"
rm -rf whisper-hinglish-apex-int8
venv/bin/python -m ctranslate2.converters.transformers --model hf --output_dir whisper-hinglish-apex-int8 \
  --quantization int8 --copy_files tokenizer.json preprocessor_config.json
cp "$HERE/LICENSE" "$HERE/NOTICE" whisper-hinglish-apex-int8/

echo "==> Zipping"
rm -f whisper-hinglish-apex-int8.zip
zip -rq -0 whisper-hinglish-apex-int8.zip whisper-hinglish-apex-int8
SIZE_MB=$(( $(stat -f%z whisper-hinglish-apex-int8.zip 2>/dev/null || stat -c%s whisper-hinglish-apex-int8.zip) / 1048576 ))
SHA=$(shasum -a 256 whisper-hinglish-apex-int8.zip | cut -d' ' -f1)
echo ""
echo "✓ $WORK/whisper-hinglish-apex-int8.zip"
echo "  HINGLISH_MODEL_MB = $SIZE_MB"
echo "  HINGLISH_MODEL_SHA256 = \"$SHA\""
