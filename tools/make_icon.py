"""Generate the Virtual Wheel application icon from the in-app artwork.

Run from the repository root:

    python tools/make_icon.py

Outputs:
    assets/virtual_wheel.png  (256 px preview / documentation)
    assets/virtual_wheel.ico  (multi-size Windows icon used by the EXE)
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import virtual_wheel_vjoy as vw  # noqa: E402

ASSETS = ROOT / "assets"
ICON_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    image = vw._draw_app_icon(256)
    png_path = ASSETS / "virtual_wheel.png"
    ico_path = ASSETS / "virtual_wheel.ico"
    image.save(png_path)
    image.save(ico_path, format="ICO", sizes=ICON_SIZES)
    print(f"Wrote {png_path} ({png_path.stat().st_size} bytes)")
    print(f"Wrote {ico_path} ({ico_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
