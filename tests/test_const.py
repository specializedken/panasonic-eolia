"""Tests for const.py's OPERATION_MODE_DESCRIPTIONS and climate entity translations.

Regression-locks that every operation_mode/fan_mode/swing_mode/swing_horizontal_mode
value stays documented -- both in the CLI's `modes` command (via
OPERATION_MODE_DESCRIPTIONS) and in the climate entity's state_attributes translations
(strings.json / translations/en.json), so a future new value can't silently go
undocumented (or show as a raw wire string in the HA UI) in either place.
"""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.eolia.const import (
    OPERATION_MODE_DESCRIPTIONS,
    WIND_DIRECTION_LEVELS,
    WIND_VOLUME_LEVELS,
    EoliaOperationMode,
    EoliaWindDirectionHorizon,
)

REPO_ROOT = Path(__file__).parent.parent


def test_every_operation_mode_has_a_description():
    for mode in EoliaOperationMode:
        assert mode.value in OPERATION_MODE_DESCRIPTIONS, f"{mode.value} undocumented"
        assert OPERATION_MODE_DESCRIPTIONS[mode.value].strip()


def test_descriptions_has_no_stale_entries():
    valid_values = {mode.value for mode in EoliaOperationMode}
    assert set(OPERATION_MODE_DESCRIPTIONS) <= valid_values


def _state_attribute_keys(translations_file: str, attribute: str) -> set[str]:
    data = json.loads(
        (REPO_ROOT / "custom_components" / "eolia" / translations_file).read_text()
    )
    return set(data["entity"]["climate"]["eolia"]["state_attributes"][attribute]["state"])


def test_strings_json_preset_mode_translations_cover_every_mode():
    valid_values = {mode.value for mode in EoliaOperationMode}
    assert _state_attribute_keys("strings.json", "preset_mode") == valid_values


def test_strings_json_fan_mode_translations_cover_every_level():
    valid_values = {str(level) for level in WIND_VOLUME_LEVELS}
    assert _state_attribute_keys("strings.json", "fan_mode") == valid_values


def test_strings_json_swing_mode_translations_cover_every_level():
    valid_values = {str(level) for level in WIND_DIRECTION_LEVELS}
    assert _state_attribute_keys("strings.json", "swing_mode") == valid_values


def test_strings_json_swing_horizontal_mode_translations_cover_every_value():
    valid_values = {mode.value for mode in EoliaWindDirectionHorizon}
    assert _state_attribute_keys("strings.json", "swing_horizontal_mode") == valid_values


def test_translations_en_json_matches_strings_json():
    for attribute in ("preset_mode", "fan_mode", "swing_mode", "swing_horizontal_mode"):
        assert _state_attribute_keys(
            "translations/en.json", attribute
        ) == _state_attribute_keys("strings.json", attribute), attribute
