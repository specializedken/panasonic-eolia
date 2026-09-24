#!/usr/bin/env python3
"""Controlled A/B experiments on the real unit -- settles the hypotheses the fuzzer raised.

Each experiment starts from an explicit baseline (everything auto/off) and changes ONE thing
at a time, printing what the server actually applied. Guarded payloads (known rules) as in
eolia_fuzz.py. Restores the starting state at the end.

    python tools/eolia_ab.py [--only vane,tiebreak,fan,shield_modes,moist,tempstep]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eolia_cli as cli  # noqa: E402
import eolia_fuzz as fz  # noqa: E402

from custom_components.eolia.api import EoliaApiClient  # noqa: E402

DELAY = 5.0
FIELDS = (*fz.STATUS_FIELDS, "humidity")


class Rig:
    def __init__(self, api: EoliaApiClient, aid: str, out):
        self.api, self.aid, self.out = api, aid, out
        self.f = fz.Fuzzer(api, aid, argparse.Namespace(seed=0, raw=False, wild=False))
        self.f.out = open("/dev/null", "w")
        self.exp = ""

    async def write(self, label: str, **changes: Any) -> dict[str, Any] | None:
        pre = await self.api.async_get_status(self.aid)
        self.f.remember(pre)
        payload, notes = self.f.build_payload(pre, changes)
        outcome, info = await self.f.put_status(payload)
        rec: dict[str, Any] = {
            "exp": self.exp,
            "label": label,
            "changes": changes,
            "pre": fz.status_view(pre),
            "outcome": outcome,
        }
        if outcome == "ok":
            new = info["response"]
            if new.operation_token:
                self.f.token = new.operation_token
            self.f.remember(new)
            view = fz.status_view(new)
            diff = {
                k: (payload[k], view[k]) for k in FIELDS if k in payload and payload[k] != view.get(k)
            }
            rec.update(response=view, server_diff=diff)
            line = f"  {label:58s} OK   now: mode={view['operation_mode']} vane={view['wind_direction']} hz={view['wind_direction_horizon']} fan={view['wind_volume']} af={view['air_flow']} sh={view['wind_shield_hit']} t={view['temperature']}"
            if diff:
                line += f"\n{'':62s}server changed: {diff}"
        else:
            rec.update(code=info.get("code"))
            line = f"  {label:58s} REJECTED {info.get('code')}"
            view = None
        print(line, flush=True)
        self.out.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self.out.flush()
        await asyncio.sleep(DELAY)
        return view

    async def baseline(self, mode: str = "Cooling") -> dict[str, Any] | None:
        """Everything explicit and 'plain': real temp, fan/louvers auto, no extras."""
        changes: dict[str, Any] = dict(
            operation_mode=mode,
            operation_status=True,
            temperature=24.0,
            wind_volume=0,
            wind_direction=0,
            wind_direction_horizon="auto",
            ai_control="off",
            air_flow="not_set",
            wind_shield_hit="not_set",
            nanoex=False,
        )
        return await self.write(f"[baseline {mode}]", **changes)


async def exp_vane(r: Rig) -> None:
    print("\n== vane: can a fixed position leave auto, and what does shield/hit do to it? ==")
    r.exp = "vane"
    await r.baseline("Cooling")
    await r.write("vane 0 -> 3 (shield/hit off)", wind_direction=3)
    await r.write("vane 3 -> 0 (back to auto)", wind_direction=0)
    await r.write("vane 0 -> 4 again (shield/hit off)", wind_direction=4)
    await r.write("set shield/hit=hit (vane currently 4)", wind_shield_hit="hit")
    await r.write("vane -> 2 while hit is on", wind_direction=2)
    await r.write("hit -> not_set alone", wind_shield_hit="not_set")
    await r.write("vane -> 2 after hit turned off", wind_direction=2)
    await r.write("vane 2 -> 6 (swing)", wind_direction=6)
    await r.write("vane 6 -> 1 (fixed overrides swing?)", wind_direction=1)
    await r.write("hit + vane=5 in ONE write", wind_shield_hit="hit", wind_direction=5)
    await r.write("shield (not hit) alone", wind_shield_hit="shield")
    await r.write("vane -> 3 while shield is on", wind_direction=3)


async def exp_tiebreak(r: Rig) -> None:
    print("\n== tie-break: shield/hit vs air_flow, per mode ==")
    r.exp = "tiebreak"
    for mode in ("Cooling", "ComfortableDehumidification", "CoolDehumidifying", "Auto"):
        print(f" -- {mode}")
        await r.baseline(mode)
        await r.write(f"{mode}: hit + air_flow=quiet in ONE write", wind_shield_hit="hit", air_flow="quiet")
        await r.baseline(mode)
        await r.write(f"{mode}: air_flow=quiet alone", air_flow="quiet")
        await r.write(f"{mode}: then hit alone (air_flow active)", wind_shield_hit="hit")
        await r.baseline(mode)
        await r.write(f"{mode}: hit alone", wind_shield_hit="hit")
        await r.write(f"{mode}: then air_flow=powerful alone (hit active)", air_flow="powerful")


async def exp_fan(r: Rig) -> None:
    print("\n== fan: what forces wind_volume to 0? ==")
    r.exp = "fan"
    await r.baseline("Cooling")
    await r.write("fan 0 -> 3 (plain)", wind_volume=3)
    await r.write("air_flow=quiet alone (fan is 3)", air_flow="quiet")
    await r.write("fan -> 4 while air_flow=quiet", wind_volume=4)
    await r.write("air_flow=not_set alone", air_flow="not_set")
    await r.write("fan -> 4 after air_flow cleared", wind_volume=4)
    await r.write("hit alone (fan is 4)", wind_shield_hit="hit")
    await r.write("fan -> 2 while hit", wind_volume=2)
    await r.write("hit -> shield alone", wind_shield_hit="shield")
    await r.write("fan -> 2 while shield", wind_volume=2)
    await r.write("shield -> not_set, fan=2 in same write", wind_shield_hit="not_set", wind_volume=2)


async def exp_shield_modes(r: Rig) -> None:
    print("\n== wind_shield_hit support per mode ==")
    r.exp = "shield_modes"
    for mode in (
        "Auto",
        "Cooling",
        "CoolDehumidifying",
        "MoistCooling",
        "ComfortableDehumidification",
        "Heating",
        "Blast",
        "ClothesDryer",
    ):
        print(f" -- {mode}")
        await r.baseline(mode)
        await r.write(f"{mode}: hit", wind_shield_hit="hit")
        await r.write(f"{mode}: shield", wind_shield_hit="shield")


async def exp_moist(r: Rig) -> None:
    print("\n== MoistCooling: does it really downgrade, and from where? ==")
    r.exp = "moist"
    for src in ("Cooling", "Heating", "ComfortableDehumidification", "Auto", "Blast"):
        await r.baseline(src)
        await r.write(f"{src} -> MoistCooling (mode only)", operation_mode="MoistCooling")
        await r.write("MoistCooling: temp 26", temperature=26.0)
    await r.write("power off", operation_status=False)
    await r.write("off -> MoistCooling (power on)", operation_mode="MoistCooling", operation_status=True)


async def exp_tempstep(r: Rig) -> None:
    print("\n== temperature step: are 0.5 steps real? ==")
    r.exp = "tempstep"
    for mode in ("Cooling", "Heating", "CoolDehumidifying", "Auto"):
        await r.baseline(mode)
        await r.write(f"{mode}: 24.5", temperature=24.5)
        await r.write(f"{mode}: 25.0", temperature=25.0)
        await r.write(f"{mode}: 25.3 (off-grid)", temperature=25.3)
        await r.write(f"{mode}: 16.5", temperature=16.5)
        await r.write(f"{mode}: 29.5", temperature=29.5)


async def exp_af_hit(r: Rig) -> None:
    print("\n== shield/hit vs each air_flow value (Cooling), both orders + same write ==")
    r.exp = "af_hit"
    for af in ("powerful", "quiet", "long"):
        print(f" -- air_flow={af}")
        await r.baseline("Cooling")
        await r.write(f"hit first", wind_shield_hit="hit")
        await r.write(f"then air_flow={af}", air_flow=af)
        await r.baseline("Cooling")
        await r.write(f"air_flow={af} first", air_flow=af)
        await r.write("then hit", wind_shield_hit="hit")
        await r.baseline("Cooling")
        await r.write(f"hit + air_flow={af} in one write", wind_shield_hit="hit", air_flow=af)


async def exp_horizon(r: Rig) -> None:
    print("\n== horizontal louver vs shield/hit (Cooling) ==")
    r.exp = "horizon"
    await r.baseline("Cooling")
    await r.write("hz -> to_left (plain)", wind_direction_horizon="to_left")
    await r.write("hit (hz is to_left)", wind_shield_hit="hit")
    await r.write("hz -> wide while hit", wind_direction_horizon="wide")
    await r.write("hit -> not_set alone", wind_shield_hit="not_set")
    await r.write("hz -> wide after hit off", wind_direction_horizon="wide")
    await r.write("hz -> auto", wind_direction_horizon="auto")
    await r.write("hz -> to_right", wind_direction_horizon="to_right")


EXPERIMENTS = {
    "af_hit": exp_af_hit,
    "horizon": exp_horizon,
    "vane": exp_vane,
    "tiebreak": exp_tiebreak,
    "fan": exp_fan,
    "shield_modes": exp_shield_modes,
    "moist": exp_moist,
    "tempstep": exp_tempstep,
}


async def main_async(args: argparse.Namespace) -> None:
    fz.RUN_DIR.mkdir(exist_ok=True)
    async with cli._new_session() as session:
        api = EoliaApiClient(session, await cli._make_auth(session))
        aid = (await api.async_get_devices())[0].appliance_id
        start = await api.async_get_status(aid)
        start_cs = await api.async_get_custom_settings(aid)
        path = fz.RUN_DIR / f"ab_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
        out = path.open("a")
        r = Rig(api, aid, out)
        r.f.remember(start)
        print(f"Logging to {path}")
        names = args.only.split(",") if args.only else list(EXPERIMENTS)
        try:
            for name in names:
                if fz.STOP_FILE.exists():
                    print("STOP file seen")
                    break
                await EXPERIMENTS[name](r)
        finally:
            await r.f.restore(start, start_cs)
            out.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--only", help="comma-separated subset of: " + ",".join(EXPERIMENTS))
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
