"""Tests for frontend.py's card registration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.eolia import frontend
from custom_components.eolia.frontend import CARD_FILE, URL_BASE, async_register_card


async def test_register_card_serves_file_and_loads_versioned_url():
    hass = MagicMock()
    hass.http.async_register_static_paths = AsyncMock()
    integration = MagicMock(version="1.2.3")

    with (
        patch.object(frontend, "async_get_integration", AsyncMock(return_value=integration)),
        patch.object(frontend, "add_extra_js_url") as add_extra_js_url,
    ):
        await async_register_card(hass)

    (configs,) = hass.http.async_register_static_paths.await_args.args
    (config,) = configs
    assert config.url_path == f"{URL_BASE}/{CARD_FILE}"
    assert Path(config.path).is_file()
    # Version in the query string is what busts browser caches on upgrade.
    add_extra_js_url.assert_called_once_with(hass, f"{URL_BASE}/{CARD_FILE}?v=1.2.3")
