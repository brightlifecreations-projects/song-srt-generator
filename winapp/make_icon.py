"""winapp/app.ico from macapp/icon-source.png (all the sizes Windows uses). Run with any Python
that has Pillow (the app's venv has it):

    .venv\\Scripts\\python winapp\\make_icon.py
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]

source = Image.open(ROOT / "macapp" / "icon-source.png").convert("RGBA")
bbox = source.getbbox()  # trim fully transparent margins so the icon fills small sizes
if bbox:
    source = source.crop(bbox)
side = max(source.size)
square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
square.paste(source, ((side - source.width) // 2, (side - source.height) // 2))
square.save(ROOT / "winapp" / "app.ico", sizes=[(s, s) for s in SIZES])
print("wrote", ROOT / "winapp" / "app.ico")
