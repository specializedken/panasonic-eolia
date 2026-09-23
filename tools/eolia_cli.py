#!/usr/bin/env python3
"""Standalone CLI to exercise every custom_components/eolia code path against the real
Eolia cloud API and real hardware, without needing a running Home Assistant instance.

Purpose: validate auth.py/api.py/models.py -- the framework-independent core of the
integration -- against the live device *before* wiring this into a real HA config flow.
Imports those modules directly from custom_components/eolia (not a reimplementation), so
there's no drift between what this tool exercises and what HA will actually run. The
only thing this deliberately does NOT exercise is coordinator.py/climate.py/etc, since
those require a running Home Assistant core (already covered by the 61-test unit suite
in tests/ instead -- this tool covers the part that suite can't: real network behavior).

Usage (run from the repo root, inside a venv with `pip install -r requirements-test.txt`):
    python tools/eolia_cli.py login
    python tools/eolia_cli.py devices
    python tools/eolia_cli.py status [APPLIANCE_ID]
    python tools/eolia_cli.py set [APPLIANCE_ID] --mode Cooling --temp 24 --fan 3
    python tools/eolia_cli.py refresh

Tokens are cached in .eolia_tokens.json at the repo root (gitignored, chmod 600 on
write) -- never commit this file or paste its contents anywhere. `set` always prints the
exact outgoing payload and asks for confirmation before sending, unless -y/--yes is
passed, since it issues a real control write against your actual AC.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from aiohttp import ClientSession, TCPConnector  # noqa: E402
from aiohttp.resolver import ThreadedResolver  # noqa: E402

from custom_components.eolia.api import EoliaApiClient  # noqa: E402
from custom_components.eolia.auth import (  # noqa: E402
    EoliaAuth,
    async_exchange_code,
    async_get_userinfo,
    build_authorize_url,
    generate_pkce_pair,
    generate_state,
    token_data_to_entry_data,
)
from custom_components.eolia.const import (  # noqa: E402
    CONF_ACCESS_TOKEN,
    CONF_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
)
from custom_components.eolia.exceptions import EoliaApiError, EoliaAuthError  # noqa: E402
from custom_components.eolia.models import EoliaCustomSettings, EoliaStatus  # noqa: E402

TOKENS_FILE = REPO_ROOT / ".eolia_tokens.json"


def _new_session() -> ClientSession:
    # aiohttp defaults to aiodns/c-ares for DNS resolution whenever aiodns is
    # importable (it's pulled in transitively for HA's hardcoded AsyncResolver
    # requirement -- see requirements-test.txt), but c-ares doesn't get along with
    # systemd-resolved's minimal stub listener at 127.0.0.53 on this host (it rejects
    # c-ares' queries with "not implemented", independent of any package version).
    # Force the plain threaded resolver (regular glibc getaddrinfo) instead, which
    # works correctly here. Unlike HA's own aiohttp_client helper -- and unlike our
    # test suite, which never makes a real network call -- this CLI genuinely needs
    # working DNS.
    return ClientSession(connector=TCPConnector(resolver=ThreadedResolver()))


def _load_tokens() -> dict[str, Any]:
    if not TOKENS_FILE.exists():
        print(f"No cached tokens at {TOKENS_FILE}. Run `login` first.", file=sys.stderr)
        raise SystemExit(1)
    return json.loads(TOKENS_FILE.read_text())


def _save_tokens(data: dict[str, Any]) -> None:
    TOKENS_FILE.write_text(json.dumps(data, indent=2))
    os.chmod(TOKENS_FILE, 0o600)


async def _make_auth(session: ClientSession) -> EoliaAuth:
    tokens = _load_tokens()

    async def _on_tokens_updated(new_tokens: dict[str, Any]) -> None:
        _save_tokens(new_tokens)

    return EoliaAuth(
        session,
        access_token=tokens[CONF_ACCESS_TOKEN],
        refresh_token=tokens[CONF_REFRESH_TOKEN],
        expires_at=tokens[CONF_EXPIRES_AT],
        on_tokens_updated=_on_tokens_updated,
    )


async def cmd_login(_args: argparse.Namespace) -> None:
    code_verifier, code_challenge = generate_pkce_pair()
    state = generate_state()
    url = build_authorize_url(code_challenge=code_challenge, state=state)
    print("1. Open this URL in a browser and log in:\n")
    print(f"   {url}\n")
    print("2. The page will fail to load after login (expected) -- that's normal.")
    print("3. Open DevTools (F12) -> Network tab, find the request to")
    print("   'panasonic-eolia://...', and copy its full Request URL.")
    print("4. Paste that URL (or just the `code` value) below.\n")
    raw = input("Paste here: ").strip()

    code = raw
    if "code=" in raw:
        parsed = urlparse(raw)
        codes = parse_qs(parsed.query).get("code")
        if codes:
            code = codes[0]

    async with _new_session() as session:
        token_data = await async_exchange_code(
            session, code=code, code_verifier=code_verifier
        )
        entry_data = token_data_to_entry_data(token_data)
        _save_tokens(entry_data)
        userinfo = await async_get_userinfo(
            session, access_token=entry_data[CONF_ACCESS_TOKEN]
        )
        member_id = userinfo.get(
            "https://club.panasonic.jp/userinfo/app_metadata", {}
        ).get("member_user_id")
        print(f"\nLogged in. member_user_id={member_id}")
        print(f"Tokens saved to {TOKENS_FILE} (chmod 600).")


async def cmd_refresh(_args: argparse.Namespace) -> None:
    async with _new_session() as session:
        auth = await _make_auth(session)
        token = await auth.async_force_refresh()
        print(f"Refreshed. New access token (truncated): {token[:12]}...")


async def cmd_devices(_args: argparse.Namespace) -> None:
    async with _new_session() as session:
        auth = await _make_auth(session)
        api = EoliaApiClient(session, auth)
        devices = await api.async_get_devices()
        for d in devices:
            print(f"{d.appliance_id}  {d.nickname!r}  {d.product_code}")


async def _resolve_appliance_id(api: EoliaApiClient, explicit: str | None) -> str:
    if explicit:
        return explicit
    devices = await api.async_get_devices()
    if len(devices) != 1:
        print(
            "Multiple (or zero) devices on this account -- pass an appliance_id "
            "explicitly. Run `devices` to list them.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return devices[0].appliance_id


def _status_dict(status: EoliaStatus) -> dict[str, Any]:
    return {
        "operation_status": status.operation_status,
        "operation_mode": status.operation_mode,
        "ai_control": status.ai_control,
        "temperature": status.temperature,
        "wind_volume": status.wind_volume,
        "wind_direction": status.wind_direction,
        "wind_direction_horizon": status.wind_direction_horizon,
        "air_flow": status.air_flow,
        "wind_shield_hit": status.wind_shield_hit,
        "nanoex": status.nanoex,
        "airquality": status.airquality,
        "inside_temp": status.inside_temp,
        "inside_humidity": status.inside_humidity,
        "outside_temp": status.outside_temp,
        "aq_name": status.aq_name,
        "aq_value": status.aq_value,
    }


async def cmd_status(args: argparse.Namespace) -> None:
    async with _new_session() as session:
        auth = await _make_auth(session)
        api = EoliaApiClient(session, auth)
        appliance_id = await _resolve_appliance_id(api, args.appliance_id)
        status = await api.async_get_status(appliance_id)
        print(json.dumps(_status_dict(status), indent=2))


async def cmd_set(args: argparse.Namespace) -> None:
    async with _new_session() as session:
        auth = await _make_auth(session)
        api = EoliaApiClient(session, auth)
        appliance_id = await _resolve_appliance_id(api, args.appliance_id)

        current = await api.async_get_status(appliance_id)
        # Mirrors coordinator.py's read-modify-write contract exactly: start from the
        # last-known status's fixed control fields, inject silence_control (write-only,
        # no readback -- see findings.md), then overlay only what was requested.
        payload = current.to_control_fields()
        payload["silence_control"] = args.silence if args.silence is not None else False

        if args.power is not None:
            payload["operation_status"] = args.power == "on"
        if args.mode is not None:
            payload["operation_mode"] = args.mode
        if args.temp is not None:
            payload["temperature"] = args.temp
        if args.fan is not None:
            payload["wind_volume"] = args.fan
        if args.swing is not None:
            payload["wind_direction"] = args.swing
        if args.swing_h is not None:
            payload["wind_direction_horizon"] = args.swing_h
        if args.ai is not None:
            payload["ai_control"] = args.ai
        if args.nanoex is not None:
            payload["nanoex"] = args.nanoex
        if args.airquality is not None:
            payload["airquality"] = args.airquality
        if args.air_flow is not None:
            payload["air_flow"] = args.air_flow
        if args.wind_shield_hit is not None:
            payload["wind_shield_hit"] = args.wind_shield_hit
        if args.humidity is not None:
            # NOT part of EoliaStatus.to_control_fields() -- deliberately excluded there
            # since it broke every other mode's PUT (see findings.md's "RESOLVED"
            # section). Injected here only for testing whether some specific mode (e.g.
            # ComfortableDehumidification/Dry) is the exception and actually requires it.
            payload["humidity"] = args.humidity

        print("About to send this control write to the real device:")
        print(json.dumps(payload, indent=2))
        if not args.yes:
            confirm = input("\nProceed? [y/N] ").strip().lower()
            if confirm != "y":
                print("Aborted.")
                return

        new_status = await api.async_set_status(appliance_id, payload)
        print("\nNew status:")
        print(json.dumps(_status_dict(new_status), indent=2))


def _custom_settings_dict(settings: EoliaCustomSettings) -> dict[str, Any]:
    return {
        "double_mode_temp": settings.double_mode_temp.to_dict(),
        "peak_cut": settings.peak_cut,
        "operation_priority": settings.operation_priority,
        "device_errstatus": settings.device_errstatus,
        "operation_token": (
            f"<redacted, len={len(settings.operation_token)}>"
            if settings.operation_token
            else None
        ),
    }


async def cmd_customsettings(args: argparse.Namespace) -> None:
    async with _new_session() as session:
        auth = await _make_auth(session)
        api = EoliaApiClient(session, auth)
        appliance_id = await _resolve_appliance_id(api, args.appliance_id)
        settings = await api.async_get_custom_settings(appliance_id)
        print(json.dumps(_custom_settings_dict(settings), indent=2))


async def cmd_set_double_temp(args: argparse.Namespace) -> None:
    async with _new_session() as session:
        auth = await _make_auth(session)
        api = EoliaApiClient(session, auth)
        appliance_id = await _resolve_appliance_id(api, args.appliance_id)

        current = await api.async_get_custom_settings(appliance_id)
        payload = current.to_control_fields()
        double_mode_temp = dict(payload["double_mode_temp"])
        if args.status is not None:
            double_mode_temp["status"] = args.status
        if args.high is not None:
            double_mode_temp["high"] = args.high
        if args.low is not None:
            double_mode_temp["low"] = args.low
        payload["double_mode_temp"] = double_mode_temp

        print("About to send this control write to the real device:")
        print(json.dumps(payload, indent=2))
        if not args.yes:
            confirm = input("\nProceed? [y/N] ").strip().lower()
            if confirm != "y":
                print("Aborted.")
                return

        new_settings = await api.async_set_custom_settings(appliance_id, payload)
        print("\nNew custom settings:")
        print(json.dumps(_custom_settings_dict(new_settings), indent=2))


def _bool_arg(value: str) -> bool:
    if value.lower() in ("true", "1", "on", "yes"):
        return True
    if value.lower() in ("false", "0", "off", "no"):
        return False
    raise argparse.ArgumentTypeError(f"expected true/false, got {value!r}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="Run the manual PKCE login flow and cache tokens")
    sub.add_parser("refresh", help="Force a refresh_token exchange")
    sub.add_parser("devices", help="List devices on the account")

    p_status = sub.add_parser("status", help="GET current status")
    p_status.add_argument("appliance_id", nargs="?", default=None)

    p_set = sub.add_parser("set", help="Read-modify-write a real control change")
    p_set.add_argument("appliance_id", nargs="?", default=None)
    p_set.add_argument("--power", choices=["on", "off"])
    p_set.add_argument("--mode", help="operation_mode wire value, e.g. Cooling")
    p_set.add_argument("--temp", type=float)
    p_set.add_argument("--fan", type=int, help="wind_volume")
    p_set.add_argument("--swing", type=int, help="wind_direction (vertical louver)")
    p_set.add_argument("--swing-h", dest="swing_h", help="wind_direction_horizon")
    p_set.add_argument("--ai", help="ai_control: off/comfortable/comfortable_econavi")
    p_set.add_argument("--nanoex", type=_bool_arg)
    p_set.add_argument("--airquality", type=_bool_arg)
    p_set.add_argument("--silence", type=_bool_arg)
    p_set.add_argument(
        "--air-flow", dest="air_flow", help="air_flow: not_set/quiet/powerful/long"
    )
    p_set.add_argument(
        "--wind-shield-hit",
        dest="wind_shield_hit",
        help="wind_shield_hit: not_set/shield/hit",
    )
    p_set.add_argument(
        "--humidity",
        type=int,
        help=(
            "EXPERIMENTAL: not part of the normal control-field contract (see "
            "findings.md) -- only for testing whether a specific mode requires it"
        ),
    )
    p_set.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    p_custom = sub.add_parser(
        "customsettings", help="GET KeepMode's double-temperature range (and peak_cut)"
    )
    p_custom.add_argument("appliance_id", nargs="?", default=None)

    p_double = sub.add_parser(
        "set-double-temp", help="Read-modify-write KeepMode's double_mode_temp range"
    )
    p_double.add_argument("appliance_id", nargs="?", default=None)
    p_double.add_argument("--status", type=_bool_arg, help="double_mode_temp.status")
    p_double.add_argument("--high", type=int, help="double_mode_temp.high (app allows 21-30)")
    p_double.add_argument("--low", type=int, help="double_mode_temp.low (app allows 16-25)")
    p_double.add_argument(
        "-y", "--yes", action="store_true", help="Skip confirmation prompt"
    )

    return parser


async def _async_main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    handlers = {
        "login": cmd_login,
        "refresh": cmd_refresh,
        "devices": cmd_devices,
        "status": cmd_status,
        "set": cmd_set,
        "customsettings": cmd_customsettings,
        "set-double-temp": cmd_set_double_temp,
    }
    try:
        await handlers[args.command](args)
    except (EoliaApiError, EoliaAuthError) as err:
        print(f"Error: {err}", file=sys.stderr)
        raise SystemExit(1) from err


def main() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
