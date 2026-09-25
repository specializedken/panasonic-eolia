"""Shared fixtures for the Eolia test suite.

Fixtures under tests/fixtures/ are real traffic captured from Kevin's account
(see docs/findings.md and docs/research-history.md's "RESOLVED" sections) -- token/credential values in
these tests are always fabricated dummies, never anything from the real
.eolia_tokens.json.
"""

from __future__ import annotations

import asyncio
import gc
import json
from pathlib import Path

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


@pytest.fixture(scope="session", autouse=True)
def _prime_pycares_shutdown_thread() -> None:
    """Force pycares' one-per-process background shutdown thread to start now.

    pycares (aiodns' backend, which aiohttp prefers whenever it's importable --
    homeassistant depends on it) keeps one global `_ChannelShutdownManager` whose
    background thread ('_run_safe_shutdown_loop') is lazily started the first time
    *any* pycares Channel object anywhere in the process is garbage-collected -- not
    when it's created. Home Assistant's own test harness setup constructs at least one
    such Channel internally (unrelated to this integration's code -- confirmed by
    isolating to test files that never touch a real aiohttp session/resolver, which
    show no issue at all), and exactly when Python's GC happens to collect it is
    inherently timing-dependent. That race can land the thread's appearance in
    threading.enumerate() during whichever test's teardown happens to run at that
    moment, and pytest_homeassistant_custom_component's `verify_cleanup` fixture (which
    diffs threading.enumerate() before/after each test) then misattributes it to that
    test as a "leaked thread". Force the destroy-and-spawn to happen here, in its own
    throwaway loop, before any test's own thread snapshot is taken, so it can never
    land on a real test.
    """

    async def _create_and_drop_a_channel() -> None:
        from aiohttp.resolver import AsyncResolver

        resolver = AsyncResolver()
        del resolver
        gc.collect()
        # Give the background thread a moment to actually register itself in
        # threading.enumerate() before this throwaway loop (and this fixture) exits.
        await asyncio.sleep(0.1)

    asyncio.run(_create_and_drop_a_channel())


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make custom_components/eolia importable as a real integration in hass tests."""
    yield


@pytest.fixture
def status_response() -> dict:
    """Real GET .../status body (2026-09-23 capture, Dry mode)."""
    return _load_fixture("status_response.json")


@pytest.fixture
def control_request() -> dict:
    """Real PUT .../status request body (2026-09-23 capture)."""
    return _load_fixture("control_request.json")


@pytest.fixture
def control_response() -> dict:
    """Real PUT .../status response body (2026-09-23 capture)."""
    return _load_fixture("control_response.json")


@pytest.fixture
def devices_response() -> dict:
    """Real GET /devices body (2026-09-23 capture)."""
    return _load_fixture("devices_response.json")


@pytest.fixture
def customsettings_response() -> dict:
    """Real GET .../customsettings body (2026-09-23 capture, no operation_token -- GET-only)."""
    return _load_fixture("customsettings_response.json")
