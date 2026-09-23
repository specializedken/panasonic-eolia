"""Tests for const.py's OPERATION_MODE_DESCRIPTIONS and its consumers.

Regression-locks that every EoliaOperationMode value stays documented -- both in the
CLI's `modes` command (via OPERATION_MODE_DESCRIPTIONS) and in the climate entity's
preset_mode state translations (strings.json / translations/en.json), so a future new
enum value can't silently go undocumented in either place.
"""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.eolia.const import OPERATION_MODE_DESCRIPTIONS, EoliaOperationMode

REPO_ROOT = Path(__file__).parent.parent


def test_every_operation_mode_has_a_description():
    for mode in EoliaOperationMode:
        assert mode.value in OPERATION_MODE_DESCRIPTIONS, f"{mode.value} undocumented"
        assert OPERATION_MODE_DESCRIPTIONS[mode.value].strip()


def test_descriptions_has_no_stale_entries():
    valid_values = {mode.value for mode in EoliaOperationMode}
    assert set(OPERATION_MODE_DESCRIPTIONS) <= valid_values


def _preset_mode_state_keys(translations_file: str) -> set[str]:
    data = json.loads((REPO_ROOT / "custom_components" / "eolia" / translations_file).read_text())
    return set(
        data["entity"]["climate"]["eolia"]["state_attributes"]["preset_mode"]["state"]
    )


def test_strings_json_preset_mode_translations_cover_every_mode():
    valid_values = {mode.value for mode in EoliaOperationMode}
    assert _preset_mode_state_keys("strings.json") == valid_values


def test_translations_en_json_matches_strings_json():
    assert _preset_mode_state_keys("translations/en.json") == _preset_mode_state_keys(
        "strings.json"
    )
