"""Copy the useful Eolia app icons out of the jadx-decompiled resources.

Usage: python tools/extract_icons.py [--out icons]

Picks the highest-density version of each wanted resource from code/res/{mipmap,drawable}-*
and writes it to <out>/<category>/<name>.<ext>, plus <out>/manifest.json. The output is
Panasonic's copyrighted artwork: it is gitignored and meant for local use only.
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


if __name__ == "__main__":
    main()
