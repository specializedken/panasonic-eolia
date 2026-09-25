"""Copy the useful Eolia app icons out of the jadx-decompiled resources.

Usage: python tools/extract_icons.py [--out icons]

Picks the highest-density version of each wanted resource from code/res/{mipmap,drawable}-*
and writes it to <out>/<category>/<name>.<ext>, plus <out>/manifest.json. The output is
Panasonic's copyrighted artwork: the default <out> (`icons/` at the repo root) is gitignored. Only
the few files the Lovelace card uses are committed, by copying them by hand into
custom_components/eolia/www/icons/ -- do not commit the rest.

It also writes <out>/rows/<translation_key>.png: the few icons the Lovelace card shows on its
settings rows (fan/louver/nanoeX/targeting), made square and recoloured. HA draws a row image
with `background-size: cover` in a 40 px circle, so a wide icon would be cropped, and the
originals are black or near-black (invisible on a dark theme). Needs Pillow; without it that
step is skipped and the card simply keeps HA's own icons on those rows.

To refresh what the card ships, copy the files it uses from <out>/modes and <out>/rows into
custom_components/eolia/www/icons/modes and .../rows (tests/test_frontend.py checks they exist).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

RES = Path(__file__).resolve().parent.parent / "code" / "resources" / "res"

# Later = better. Non-density folders (plain "mipmap"/"drawable") rank lowest.
DENSITY_RANK = ["ldpi", "mdpi", "hdpi", "xhdpi", "xxhdpi", "xxxhdpi"]

CATEGORIES: dict[str, str] = {
    "modes": r"v6_drive_mode_.*|v6_polygon_.*|v6_weekly_style_(clean|stop)"
    r"|background_drive_mode_select_.*|button_send_.*",
    "louver_vertical": r"v7_(main_)?upward_wind_direction_.*|main_icfuukoutate.*"
    r"|ic_wind_v_swing|icon_updown_swing|v6_circle_seek_v_swing|v7_circle_seek_v_swing2",
    "louver_horizontal": r"ic_wind_hor_swing.*|icfuukouyoko_.*|main_icfuukouyoko_swing",
    "airflow": r"quiet.*|powerful.*|wind_shield|wind_hit|swing",
    "features": r"nanoe_logo|nanoex_logo|econavi|btnaieconavi.|v6_operation_nanoe"
    r"|ai_air_quality|powersave|ic_ai_scene_item",
    "humidity": r"humidity|dehumidfying\d+|humidfying\d+",
}


# Icons shown on the card's settings rows, keyed by the entity's translation_key (so the card
# can find them without a table of its own): source file under <out>/<category>/.
ROW_ICONS: dict[str, str] = {
    "vertical_louver": "louver_vertical/icon_updown_swing.png",
    "horizontal_louver": "louver_horizontal/ic_wind_hor_swing.png",
    "nanoex": "features/v6_operation_nanoe.png",
    "wind_shield_hit": "airflow/wind_hit.png",
}
ROW_ICON_SIZE = 128
# The app's own slate blue-grey (the louver icons are already this colour); readable on both
# light and dark themes, unlike the black originals.
ROW_ICON_COLOR = (105, 124, 146)
# HA clips the image to a circle, so keep the glyph's bounding-box half-diagonal inside it.
ROW_ICON_MAX_HALF_DIAGONAL = 0.44


def make_row_icons(out: Path) -> int:
    """Write square, recoloured copies of ROW_ICONS to <out>/rows/. Returns how many."""
    try:
        from PIL import Image
    except ImportError:
        print("Pillow not installed -- skipping the row icons (pip install Pillow)")
        return 0
    rows = out / "rows"
    rows.mkdir(parents=True, exist_ok=True)
    made = 0
    for key, relative in ROW_ICONS.items():
        source = out / relative
        if not source.exists():
            continue
        alpha = Image.open(source).convert("RGBA").getchannel("A")
        box = alpha.getbbox()
        if box is None:
            continue
        alpha = alpha.crop(box)
        # Some originals are semi-transparent black; stretch so the glyph is solid.
        peak = alpha.getextrema()[1]
        if 0 < peak < 255:
            alpha = alpha.point(lambda a: min(255, round(a * 255 / peak)))
        w, h = alpha.size
        scale = ROW_ICON_MAX_HALF_DIAGONAL * 2 * ROW_ICON_SIZE / (w * w + h * h) ** 0.5
        alpha = alpha.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
        canvas = Image.new("RGBA", (ROW_ICON_SIZE, ROW_ICON_SIZE), ROW_ICON_COLOR + (0,))
        glyph = Image.new("RGBA", alpha.size, ROW_ICON_COLOR + (255,))
        glyph.putalpha(alpha)
        canvas.paste(
            glyph,
            ((ROW_ICON_SIZE - alpha.width) // 2, (ROW_ICON_SIZE - alpha.height) // 2),
            glyph,
        )
        canvas.save(rows / f"{key}.png")
        made += 1
    return made


def _density(folder: str) -> int:
    for i, name in enumerate(DENSITY_RANK):
        if folder.endswith(f"-{name}"):
            return i
    return -1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="icons")
    args = parser.parse_args()
    out = Path(args.out)

    best: dict[str, tuple[int, Path]] = {}
    for folder in sorted(RES.iterdir()):
        if not folder.is_dir() or not re.match(r"(mipmap|drawable)", folder.name):
            continue
        if "ldrtl" in folder.name or "watch" in folder.name:
            continue
        for path in folder.iterdir():
            rank = _density(folder.name)
            if path.stem not in best or rank > best[path.stem][0]:
                best[path.stem] = (rank, path)

    manifest: dict[str, list[dict[str, str]]] = {}
    for category, pattern in CATEGORIES.items():
        regex = re.compile(pattern)
        for name in sorted(best):
            if not regex.fullmatch(name):
                continue
            src = best[name][1]
            dest = out / category / f"{name}{src.suffix}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            manifest.setdefault(category, []).append(
                {"name": name, "file": str(dest.relative_to(out)), "source": src.parent.name}
            )

    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    for category, items in manifest.items():
        print(f"{category:18} {len(items)}")
    print(f"{'rows':18} {make_row_icons(out)}")


if __name__ == "__main__":
    main()
