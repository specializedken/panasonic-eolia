#!/usr/bin/env python3
"""Random-walk fuzzer for the Eolia control API -- finds impossible/ignored combinations.

Drives REAL writes against the real unit (like eolia_cli.py's `set`), one random change at
a time, and logs every outcome to a JSONL file for `eolia_fuzz_report.py` to mine. Each
step is: GET status -> pick a random change -> read-modify-write PUT -> classify:

  ok        200, response matches what was sent
  modified  200, but the response differs from the payload (silently ignored / substituted
            / server-forced side effects) -- this is the "accepted but not applied" class
  rejected  4xx/5xx with an E-21291-* code

Two payload modes:
  guarded (default)  applies every rule already known (coordinator.py's): Dry/ClothesDryer
                     force temperature=0.0, Dry needs humidity, Nanoe is sent as Blast, a
                     bare power-on uses the last real mode, the clean family is stopped with
                     the app's normalized stop body, the known-unsupported modes are not
                     picked. So a rejection here is a NEW finding, not a rediscovery.
  --raw              plain read-modify-write, only chaining operation_token. Rediscovers
                     the known constraints too (useful as a cross-check of the guards).
  --wild             additionally probes out-of-domain values (implies nothing else).

Usage (repo root, venv active):
    python tools/eolia_fuzz.py --steps 40 --seed 1            # pilot
    touch fuzz_runs/STOP                                      # graceful early stop + restore

Never logs tokens or the appliance id. Restores the starting state at the end (best effort).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import eolia_cli as cli  # noqa: E402

from custom_components.eolia.api import EoliaApiClient  # noqa: E402
from custom_components.eolia.const import (  # noqa: E402
    CLEAN_FAMILY_MODES,
    ERROR_CODE_DEVICE_LOCKED,
    NO_TARGET_TEMPERATURE_MODES,
    EoliaAirFlow,
    EoliaAiControl,
    EoliaOperationMode as Mode,
    EoliaWindDirectionHorizon,
    EoliaWindShieldHit,
)
from custom_components.eolia.exceptions import (  # noqa: E402
    EoliaApiError,
    EoliaAuthError,
    EoliaNetworkError,
)
from custom_components.eolia.models import EoliaStatus  # noqa: E402

RUN_DIR = REPO_ROOT / "fuzz_runs"
STOP_FILE = RUN_DIR / "STOP"

STATUS_FIELDS = (
    "operation_status",
    "operation_mode",
    "temperature",
    "wind_volume",
    "wind_direction",
    "wind_direction_horizon",
    "ai_control",
    "nanoex",
    "airquality",
    "air_flow",
    "wind_shield_hit",
    "timer_value",
)

# Known-unsupported on CS-712DX2-W (functions flags false / live-rejected): not picked in
# guarded mode. KeepMode is entered through /customsettings, never via /status.
KNOWN_BAD_MODES = {
    Mode.DEHUMIDIFYING,
    Mode.AUTO_TEMP_CONTROL,
    Mode.KEEP_HEATING,
    Mode.SMELL_CARE_SPOT,
    Mode.KEEP_MODE,
    Mode.NANOE,
    Mode.STOP,
    Mode.OTHER,
}

# The app's own normalized stop body (ControlFetchCommandRHRequest.setData, status=false).
NORMALIZED_STOP = {
    "operation_status": False,
    "operation_mode": "Auto",
    "temperature": 16.0,
    "wind_volume": 0,
    "wind_direction": 0,
    "wind_direction_horizon": "auto",
}

FIELD_WEIGHTS = {
    "operation_mode": 6,
    "operation_status": 3,
    "temperature": 3,
    "wind_volume": 2,
    "wind_direction": 2,
    "wind_direction_horizon": 2,
    "ai_control": 3,
    "nanoex": 2,
    "airquality": 1,
    "air_flow": 2,
    "wind_shield_hit": 2,
    "silence_control": 1,
}


def status_view(s: EoliaStatus) -> dict[str, Any]:
    view = {f: getattr(s, f) for f in STATUS_FIELDS}
    view["humidity"] = s.humidity
    view["inside_temp"] = s.inside_temp
    view["inside_humidity"] = s.inside_humidity
    view["outside_temp"] = s.outside_temp
    return view


class Fuzzer:
    def __init__(self, api: EoliaApiClient, appliance_id: str, args: argparse.Namespace):
        self.api = api
        self.aid = appliance_id
        self.args = args
        self.rng = random.Random(args.seed)
        self.token: str | None = None
        self.silence = False
        self.temp_cache = 24.0
        self.humidity_cache = 50
        self.last_mode = Mode.AUTO.value
        self.last_put: dict[str, Any] | None = None
        self.flags: dict[str, bool] = {}
        self.out = None

    # ------------------------------------------------------------------ sampling
    def mode_choices(self) -> list[str]:
        modes = []
        for m in Mode:
            if not self.args.raw and not self.args.wild and m in KNOWN_BAD_MODES:
                continue
            if m in (Mode.STOP, Mode.OTHER, Mode.NANOE) and not self.args.wild:
                continue
            modes.append(m.value)
        return modes

    def sample(self, field: str) -> Any:
        r = self.rng
        wild = self.args.wild and r.random() < 0.15
        if field == "operation_mode":
            modes = self.mode_choices()
            weights = [0.4 if Mode(m) in CLEAN_FAMILY_MODES else 1.0 for m in modes]
            return r.choices(modes, weights)[0]
        if field == "operation_status":
            return r.random() < 0.55
        if field == "temperature":
            if wild:
                return r.choice([0.0, 15.0, 15.5, 30.5, 31.0, 35.0])
            t = float(r.randint(16, 30))
            if r.random() < 0.3 and t < 30:
                t += 0.5
            return t
        if field == "wind_volume":
            return r.choice([-1, 6, 7]) if wild else r.randint(0, 5)
        if field == "wind_direction":
            return r.choice([-1, 7, 8]) if wild else r.randint(0, 6)
        if field == "wind_direction_horizon":
            return r.choice([e.value for e in EoliaWindDirectionHorizon])
        if field == "ai_control":
            return r.choice([e.value for e in EoliaAiControl])
        if field in ("nanoex", "airquality", "silence_control"):
            return r.random() < 0.5
        if field == "air_flow":
            return r.choice([e.value for e in EoliaAirFlow])
        if field == "wind_shield_hit":
            return r.choice([e.value for e in EoliaWindShieldHit])
        if field == "humidity":
            return r.choice(range(30, 85, 5)) if wild else r.choice([50, 55, 60])
        raise KeyError(field)

    def gen_status_changes(self, pre: EoliaStatus) -> dict[str, Any]:
        r = self.rng
        k = r.choice([1, 1, 1, 2, 2, 3])
        pool = dict(FIELD_WEIGHTS)
        # airquality is flagged unsupported on this model: only rarely probe it.
        if not self.flags.get("airquality", True) and not self.args.raw:
            pool["airquality"] = 0.15
        changes: dict[str, Any] = {}
        for _ in range(k):
            if not pool:
                break
            f = r.choices(list(pool), list(pool.values()))[0]
            pool.pop(f)
            changes[f] = self.sample(f)
        # The natural way to change mode is to power on into it; sometimes don't, to test
        # mode changes while the unit is off.
        if "operation_mode" in changes and "operation_status" not in changes:
            if r.random() < 0.7:
                changes["operation_status"] = True
        return changes

    # ------------------------------------------------------------------ payloads
    def build_payload(
        self, pre: EoliaStatus, changes: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str]]:
        notes: list[str] = []
        payload = pre.to_control_fields()
        payload["silence_control"] = self.silence
        if self.token is not None:
            payload["operation_token"] = self.token
        guarded = not self.args.raw
        cur_mode = payload["operation_mode"]

        # Power off while a clean-family mode is running: only the normalized body works.
        if (
            guarded
            and changes.get("operation_status") is False
            and "operation_mode" not in changes
            and cur_mode in CLEAN_FAMILY_MODES
        ):
            payload.update(NORMALIZED_STOP)
            notes.append("normalized_stop")
            changes = {k: v for k, v in changes.items() if k != "operation_status"}

        payload.update(changes)

        if guarded:
            # bare power-on from Stop -> last real mode
            if (
                payload["operation_status"]
                and payload["operation_mode"] in (Mode.STOP, Mode.OTHER)
                and "operation_mode" not in changes
            ):
                payload["operation_mode"] = self.last_mode
                notes.append("power_on_last_mode")
            if payload["operation_mode"] == Mode.NANOE:
                payload["operation_mode"] = Mode.BLAST.value
                notes.append("nanoe_to_blast")
            if payload["operation_mode"] in (Mode.STOP, Mode.OTHER):
                # A carried "Stop" is rejected (E-21291-01711) even for an unrelated
                # change while off; keep status as-is, swap in the last real mode.
                payload["operation_mode"] = self.last_mode
                notes.append("stop_mode_replaced")
            mode = payload["operation_mode"]
            if mode in NO_TARGET_TEMPERATURE_MODES:
                if payload["temperature"] != 0.0:
                    notes.append("temp_forced_0")
                payload["temperature"] = 0.0
            elif payload["temperature"] == 0.0 and "temperature" not in changes:
                payload["temperature"] = self.temp_cache
                notes.append("temp_from_cache")
            if mode == Mode.COMFORTABLE_DEHUMIDIFICATION:
                payload["humidity"] = self.rng.choice([50, 55, 60]) if (
                    "operation_mode" in changes and self.rng.random() < 0.5
                ) else self.humidity_cache
                notes.append("dry_humidity")
            else:
                payload.pop("humidity", None)
        else:
            if payload["operation_mode"] == Mode.COMFORTABLE_DEHUMIDIFICATION:
                payload.setdefault("humidity", self.humidity_cache)
        self.silence = payload["silence_control"]
        return payload, notes

    # ------------------------------------------------------------------ step
    async def put_status(self, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """PUT with one lockout wait / one network retry. Returns (outcome, info)."""
        attempts = 0
        while True:
            attempts += 1
            try:
                new = await self.api.async_set_status(self.aid, payload)
                return "ok", {"response": new, "attempts": attempts}
            except EoliaApiError as err:
                if isinstance(err, EoliaNetworkError) and attempts < 2:
                    await asyncio.sleep(3)
                    continue
                if err.code == ERROR_CODE_DEVICE_LOCKED and attempts < 2:
                    self.log_event("lockout", {"payload_token_len": len(self.token or "")})
                    await asyncio.sleep(125)
                    continue
                return "rejected", {
                    "http": err.status,
                    "code": err.code,
                    "message": err.message,
                    "attempts": attempts,
                }

    def log_event(self, kind: str, data: dict[str, Any]) -> None:
        rec = {"kind": kind, "t": time.time(), **data}
        self.out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.out.flush()

    def remember(self, s: EoliaStatus) -> None:
        if s.temperature:
            self.temp_cache = s.temperature
        if s.operation_mode == Mode.COMFORTABLE_DEHUMIDIFICATION and s.humidity is not None:
            self.humidity_cache = s.humidity
        if s.operation_status and s.operation_mode not in (
            Mode.STOP,
            Mode.OTHER,
            Mode.NANOE,
            *CLEAN_FAMILY_MODES,
        ):
            self.last_mode = str(s.operation_mode)

    async def status_step(self, i: int) -> str:
        pre = await self.api.async_get_status(self.aid)
        pre_v = status_view(pre)
        drift = None
        if self.last_put is not None:
            drift = {
                f: {"put_response": self.last_put.get(f), "next_get": pre_v.get(f)}
                for f in (*STATUS_FIELDS, "humidity")
                if self.last_put.get(f) != pre_v.get(f)
            }
        self.remember(pre)
        changes = self.gen_status_changes(pre)
        if (
            not self.args.raw
            and pre.operation_mode == Mode.KEEP_MODE
            and "operation_mode" not in changes
        ):
            # Known: any /status write that carries KeepMode back is rejected; a write that
            # changes the mode works. So a guarded walk escapes by picking a real mode.
            changes["operation_mode"] = self.sample("operation_mode")
            changes["operation_status"] = True
        payload, notes = self.build_payload(pre, changes)
        t0 = time.time()
        outcome, info = await self.put_status(payload)
        rec: dict[str, Any] = {
            "kind": "status",
            "i": i,
            "t": t0,
            "elapsed": round(time.time() - t0, 2),
            "pre": pre_v,
            "drift": drift or None,
            "changes": changes,
            "guards": notes,
            "payload": {k: v for k, v in payload.items() if k != "operation_token"},
            "sent_token": "operation_token" in payload,
            "outcome": outcome,
        }
        if outcome == "ok":
            new: EoliaStatus = info["response"]
            new_v = status_view(new)
            self.last_put = new_v
            if new.operation_token:
                self.token = new.operation_token
            self.remember(new)
            mismatch = {}
            for f in (*STATUS_FIELDS, "humidity"):
                if f not in payload:
                    continue
                if payload[f] != new_v.get(f):
                    mismatch[f] = {
                        "sent": payload[f],
                        "got": new_v.get(f),
                        "requested": f in changes,
                    }
            rec["response"] = new_v
            rec["mismatch"] = mismatch
            if mismatch:
                rec["outcome"] = "modified"
        else:
            rec.update({k: v for k, v in info.items() if k != "response"})
            self.last_put = None  # unknown what the server holds now; re-sync via next GET
        self.out.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self.out.flush()
        return self.summarize(i, rec)

    def summarize(self, i: int, rec: dict[str, Any]) -> str:
        ch = ",".join(f"{k}={v}" for k, v in rec["changes"].items())
        head = f"[{i:04d}] {rec['outcome']:<8} {rec['pre']['operation_mode']}/{rec['pre']['operation_status']} <- {ch}"
        if rec["outcome"] == "rejected":
            head += f"  !! {rec.get('code')}"
        elif rec["outcome"] == "modified":
            head += "  ~~ " + ",".join(
                f"{f}:{m['sent']}->{m['got']}" for f, m in rec["mismatch"].items()
            )
        return head

    # ------------------------------------------------------------------ customsettings
    async def cs_step(self, i: int) -> str:
        r = self.rng
        cur = await self.api.async_get_custom_settings(self.aid)
        pre_status = await self.api.async_get_status(self.aid)
        pre_v = status_view(pre_status)
        dm = cur.double_mode_temp
        pre_cs = dm.to_dict()
        roll = r.random()
        if roll < 0.55:  # enable with a random range
            low = r.randint(16, 25)
            high = r.randint(max(21, low + 5), 30) if low + 5 <= 30 else 30
            new = {"status": True, "low": low, "high": high}
        elif roll < 0.8:  # disable
            new = {"status": False, "low": dm.low, "high": dm.high}
        elif roll < 0.9:  # too-narrow probe
            low = r.randint(16, 25)
            new = {"status": True, "low": low, "high": min(30, max(21, low + r.randint(0, 4)))}
        else:  # adjust one bound while (maybe) on
            new = {"status": dm.status, "low": dm.low, "high": dm.high}
            if r.random() < 0.5:
                new["low"] = r.randint(16, 25)
            else:
                new["high"] = r.randint(21, 30)
            if new["status"] and (new["low"] == 0 or new["high"] == 0):
                new["low"], new["high"] = 23, 28
        payload = cur.to_control_fields()
        payload["double_mode_temp"] = new
        if self.token is not None:
            payload["operation_token"] = self.token
        t0 = time.time()
        rec: dict[str, Any] = {
            "kind": "customsettings",
            "i": i,
            "t": t0,
            "pre": pre_v,
            "pre_cs": pre_cs,
            "changes": {"double_mode_temp": new},
            "payload": {k: v for k, v in payload.items() if k != "operation_token"},
            "sent_token": self.token is not None,
        }
        attempts = 0
        while True:
            attempts += 1
            try:
                res = await self.api.async_set_custom_settings(self.aid, payload)
                if res.operation_token:
                    self.token = res.operation_token
                rec["outcome"] = "ok"
                rec["response_cs"] = res.double_mode_temp.to_dict()
                exp = new if new["status"] else {"status": False}
                got = res.double_mode_temp.to_dict()
                if any(got.get(k) != v for k, v in exp.items()):
                    rec["outcome"] = "modified"
                    rec["mismatch"] = {"sent": new, "got": got}
                break
            except EoliaApiError as err:
                if err.code == ERROR_CODE_DEVICE_LOCKED and attempts < 2:
                    self.log_event("lockout", {})
                    await asyncio.sleep(125)
                    continue
                rec.update(
                    outcome="rejected", http=err.status, code=err.code, message=err.message
                )
                break
        try:
            post = await self.api.async_get_status(self.aid)
            rec["post"] = status_view(post)
            self.remember(post)
            self.last_put = None
        except EoliaApiError as err:
            rec["post_error"] = str(err)
        self.out.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self.out.flush()
        tail = f"  !! {rec.get('code')}" if rec["outcome"] == "rejected" else ""
        if rec["outcome"] == "modified":
            tail = f"  ~~ sent={rec['mismatch']['sent']} got={rec['mismatch']['got']}"
        return (
            f"[{i:04d}] {rec['outcome']:<8} CS {pre_cs} -> {new}{tail}  "
            f"(unit now {rec.get('post', {}).get('operation_mode')})"
        )

    # ------------------------------------------------------------------ restore
    async def restore(self, start: EoliaStatus, start_cs: Any) -> None:
        print("Restoring starting state...")
        try:
            cs = await self.api.async_get_custom_settings(self.aid)
            if cs.double_mode_temp.status and not start_cs.double_mode_temp.status:
                payload = cs.to_control_fields()
                payload["double_mode_temp"] = {"status": False, "low": 0, "high": 0}
                if self.token:
                    payload["operation_token"] = self.token
                res = await self.api.async_set_custom_settings(self.aid, payload)
                if res.operation_token:
                    self.token = res.operation_token
                await asyncio.sleep(4)
            cur = await self.api.async_get_status(self.aid)
            payload = cur.to_control_fields()
            payload["silence_control"] = self.silence
            if self.token:
                payload["operation_token"] = self.token
            orig = start.to_control_fields()
            if not start.operation_status:
                if cur.operation_mode in CLEAN_FAMILY_MODES:
                    payload.update(NORMALIZED_STOP)
                elif cur.operation_status:
                    payload["operation_status"] = False
            else:
                payload.update(orig)
                if payload["operation_mode"] == Mode.NANOE:
                    payload["operation_mode"] = Mode.BLAST.value
                if payload["operation_mode"] == Mode.COMFORTABLE_DEHUMIDIFICATION:
                    payload["humidity"] = start.humidity or self.humidity_cache
                else:
                    payload.pop("humidity", None)
            res = await self.api.async_set_status(self.aid, payload)
            print(f"Restored: mode={res.operation_mode} on={res.operation_status}")
        except (EoliaApiError, EoliaAuthError) as err:
            print(f"!! restore failed: {err}", file=sys.stderr)


async def main_async(args: argparse.Namespace) -> None:
    RUN_DIR.mkdir(exist_ok=True)
    STOP_FILE.unlink(missing_ok=True)
    async with cli._new_session() as session:
        auth = await cli._make_auth(session)
        api = EoliaApiClient(session, auth)
        devices = await api.async_get_devices()
        dev = devices[0]
        fz = Fuzzer(api, dev.appliance_id, args)
        try:
            fz.flags = await api.async_get_functions(dev.product_code)
        except EoliaApiError:
            fz.flags = {}
        start = await api.async_get_status(dev.appliance_id)
        start_cs = await api.async_get_custom_settings(dev.appliance_id)
        fz.remember(start)
        path = RUN_DIR / f"run_{time.strftime('%Y%m%d_%H%M%S')}_seed{args.seed}.jsonl"
        fz.out = path.open("a")
        fz.log_event(
            "start",
            {
                "args": vars(args),
                "product_code": dev.product_code,
                "flags": fz.flags,
                "start": status_view(start),
                "start_cs": start_cs.double_mode_temp.to_dict(),
            },
        )
        print(f"Logging to {path}")
        print(f"Start state: {status_view(start)}  cs={start_cs.double_mode_temp.to_dict()}")
        consecutive_errors = 0
        try:
            for i in range(1, args.steps + 1):
                if STOP_FILE.exists():
                    print("STOP file seen, ending early.")
                    break
                try:
                    if fz.rng.random() < args.cs_ratio:
                        line = await fz.cs_step(i)
                    else:
                        line = await fz.status_step(i)
                except EoliaAuthError as err:
                    print(f"auth failure, aborting: {err}", file=sys.stderr)
                    break
                except EoliaApiError as err:
                    consecutive_errors += 1
                    print(f"[{i:04d}] infra error on GET: {err}")
                    if consecutive_errors >= 8:
                        print("too many consecutive infra errors, aborting")
                        break
                    await asyncio.sleep(10)
                    continue
                consecutive_errors = 0
                print(line, flush=True)
                await asyncio.sleep(args.delay)
        finally:
            if not args.no_restore:
                await fz.restore(start, start_cs)
            fz.log_event("end", {})
            fz.out.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--steps", type=int, default=40)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--delay", type=float, default=5.0, help="seconds between steps")
    p.add_argument("--cs-ratio", type=float, default=0.08, help="fraction of /customsettings steps")
    p.add_argument("--raw", action="store_true", help="no known-rule guards")
    p.add_argument("--wild", action="store_true", help="also probe out-of-domain values")
    p.add_argument("--no-restore", action="store_true")
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
