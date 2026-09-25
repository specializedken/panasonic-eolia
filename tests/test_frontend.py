"""Tests for frontend.py's card registration."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.eolia import frontend
from custom_components.eolia.frontend import (
    CARD_FILE,
    ICONS_URL,
    URL_BASE,
    async_register_card,
)

_WWW = Path(frontend.__file__).parent / "www"


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
    card, icons = configs
    assert card.url_path == f"{URL_BASE}/{CARD_FILE}"
    assert Path(card.path).is_file()
    # The icons the card uses are served from the integration too, so it has them out of the box.
    assert icons.url_path == ICONS_URL == f"{URL_BASE}/icons"
    assert Path(icons.path).is_dir()
    # Version in the query string is what busts browser caches on upgrade.
    add_extra_js_url.assert_called_once_with(hass, f"{URL_BASE}/{CARD_FILE}?v=1.2.3")


def test_every_icon_the_card_references_is_shipped():
    """The card's default icon path points at the shipped folder; a name typo or a forgotten
    file would silently degrade to mdi icons, so check the files are really there."""
    card = (_WWW / CARD_FILE).read_text(encoding="utf8")
    modes = set(re.findall(r'icon: "(v6_[a-z_]+)"', card))
    rows = set(re.findall(r'"([a-z_]+)"', re.search(r"ROW_IMAGE_KEYS = \[([^\]]*)\]", card).group(1)))
    assert modes and rows  # the patterns above must actually find something
    for name in modes:
        assert (_WWW / "icons" / "modes" / f"{name}.png").is_file(), name
    for name in rows:
        assert (_WWW / "icons" / "rows" / f"{name}.png").is_file(), name


def test_the_card_defaults_to_the_shipped_icons_path():
    card = (_WWW / CARD_FILE).read_text(encoding="utf8")
    assert f'DEFAULT_ICON_BASE = "{ICONS_URL}"' in card
