#!/usr/bin/env python3
"""Targeted probe: which /status payloads work while the unit is in KeepMode?

The fuzzer found that any /status write carrying operation_mode=KeepMode back is rejected
(E-21291-01711). This tries alternatives for a plain fan-speed change from inside KeepMode.
"""
import argparse, asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import eolia_cli as cli
import eolia_fuzz as fz
from custom_components.eolia.api import EoliaApiClient
from custom_components.eolia.exceptions import EoliaApiError

async def main():
    async with cli._new_session() as session:
        api = EoliaApiClient(session, await cli._make_auth(session))
        dev = (await api.async_get_devices())[0]
        aid = dev.appliance_id
        start = await api.async_get_status(aid)
        start_cs = await api.async_get_custom_settings(aid)
        args = argparse.Namespace(seed=0, raw=False, wild=False)
        f = fz.Fuzzer(api, aid, args)
        f.out = open("/dev/null", "w")
        f.remember(start)

        async def enter_keepmode():
            cs = await api.async_get_custom_settings(aid)
            p = cs.to_control_fields()
            p["double_mode_temp"] = {"status": True, "low": 23, "high": 28}
            if f.token: p["operation_token"] = f.token
            r = await api.async_set_custom_settings(aid, p)
            f.token = r.operation_token or f.token
            await asyncio.sleep(5)
            return await api.async_get_status(aid)

        variants = {
            "A carry KeepMode, fan=3": lambda p: p.update(wind_volume=3),
            "B omit operation_mode, fan=3": lambda p: (p.pop("operation_mode"), p.update(wind_volume=3)),
            "C mode=Auto, fan=3": lambda p: p.update(operation_mode="Auto", wind_volume=3),
            "D mode=Other, fan=3": lambda p: p.update(operation_mode="Other", wind_volume=3),
            "E carry KeepMode, status=False": lambda p: p.update(operation_status=False),
            "F mode=Stop status=False": lambda p: p.update(operation_mode="Stop", operation_status=False),
        }
        try:
            for name, mutate in variants.items():
                cur = await enter_keepmode()
                if cur.operation_mode != "KeepMode":
                    print(f"{name}: could not enter KeepMode (got {cur.operation_mode}), skipping"); continue
                payload = cur.to_control_fields()
                payload["silence_control"] = f.silence
                payload["temperature"] = cur.temperature
                if f.token: payload["operation_token"] = f.token
                mutate(payload)
                try:
                    new = await api.async_set_status(aid, payload)
                    if new.operation_token: f.token = new.operation_token
                    print(f"{name}: OK  -> mode={new.operation_mode} on={new.operation_status} fan={new.wind_volume}")
                except EoliaApiError as e:
                    print(f"{name}: REJECTED {e.code}")
                await asyncio.sleep(6)
        finally:
            await f.restore(start, start_cs)

asyncio.run(main())
