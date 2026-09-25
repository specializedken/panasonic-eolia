"""Serves and registers the bundled `eolia-card` Lovelace card.

The card ships inside the integration (www/eolia-card.js) so installing the integration
installs the card too -- no separate HACS frontend repo, no manual resource step.

Registration uses `add_extra_js_url` rather than a Lovelace resource entry: the frontend
loads the module on every page load regardless of whether the user's dashboards are in
storage or YAML mode, and there is no resource table to keep de-duplicated across restarts.
"""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

CARD_FILE = "eolia-card.js"
URL_BASE = f"/{DOMAIN}_static"
# The app icons the card uses (modes/ and rows/), shipped inside the integration so the card
# has them out of the box. The card's default `icons` path points here.
ICONS_URL = f"{URL_BASE}/icons"
_WWW_DIR = Path(__file__).parent / "www"


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card's JS and icons, and have the frontend load the card.

    The integration version is appended as a query string so an upgrade busts browser
    caches (the JS itself is served with cache headers off, but pages that already embed
    the old module URL would otherwise keep it).
    """
    integration = await async_get_integration(hass, DOMAIN)
    url = f"{URL_BASE}/{CARD_FILE}"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(url, str(_WWW_DIR / CARD_FILE), cache_headers=False),
            StaticPathConfig(ICONS_URL, str(_WWW_DIR / "icons"), cache_headers=True),
        ]
    )
    add_extra_js_url(hass, f"{url}?v={integration.version}")
