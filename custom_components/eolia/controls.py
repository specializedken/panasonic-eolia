"""Which controls apply to the unit right now.

The single source of truth for "what can the user meaningfully change in this state" -- the
Lovelace card renders from this (via the operation_mode sensor's `controls` attribute) rather
than keeping a copy of the rules in JavaScript, and automations can use it too. Every rule is
a live-confirmed server behaviour recorded in const.py / live_captures/39; none is a guess.

Control ids are the integration's own entity `translation_key`s where a control is a separate
entity, plus three ids for the climate entity's own features (temperature, fan, louvers).
Pure functions over EoliaStatus: no Home Assistant imports, so they're trivially testable.
"""

from __future__ import annotations

from .const import (
    AI_UNSUPPORTED_MODES,
    AIR_FLOW_UNSUPPORTED_MODES,
    CLEAN_FAMILY_MODES,
    NO_TARGET_TEMPERATURE_MODES,
    NOT_SET,
    SHIELD_HIT_UNSUPPORTED_MODES,
    EoliaOperationMode,
)
from .models import EoliaStatus

# Climate-entity features (not separate entities).
TEMPERATURE = "temperature"
FAN = "fan"
LOUVERS = "louvers"
# Separate entities, identified by translation_key.
AI_MODE = "ai_mode"
AIR_FLOW = "air_flow"
WIND_SHIELD_HIT = "wind_shield_hit"
NANOEX = "nanoex"
SILENCE_CONTROL = "silence_control"
AIR_QUALITY_MONITOR = "air_quality_monitor"
DRY_HUMIDITY_TARGET = "dry_humidity_target"
DOUBLE_TEMP_ENABLED = "double_temp_enabled"
DOUBLE_TEMP_LOW = "double_temp_low"
DOUBLE_TEMP_HIGH = "double_temp_high"


def applicable_controls(status: EoliaStatus) -> tuple[str, ...]:
    """Ids of the controls that currently do something, in a stable order.

    Mode selection itself (climate hvac_mode/preset_mode) is always available and not listed.
    """
    mode = status.operation_mode

    # KeepMode is a /status dead end: every /status write that carries it back is rejected, so
    # only the /customsettings controls (and leaving via the mode picker) work.
    if mode == EoliaOperationMode.KEEP_MODE:
        return (DOUBLE_TEMP_ENABLED, DOUBLE_TEMP_LOW, DOUBLE_TEMP_HIGH)

    # Off, or one of the clean modes (which run the unit while reporting operation_status
    # False and override nearly every setting on entry): a /status write is accepted but
    # mostly not applied, and the integration refuses it. Only the double-temperature switch
    # works, since it powers the unit on into KeepMode.
    if not status.operation_status or mode in (*CLEAN_FAMILY_MODES, EoliaOperationMode.STOP):
        return (DOUBLE_TEMP_ENABLED,)

    # Nanoe is Blast + nanoeX read back under another name; it follows Blast's rules.
    effective = EoliaOperationMode.BLAST if mode == EoliaOperationMode.NANOE else mode

    controls: list[str] = []
    if mode not in NO_TARGET_TEMPERATURE_MODES:
        controls.append(TEMPERATURE)
    # shield/hit forces fan and both louvers to auto and ignores writes to them; fan speed is
    # also only honoured while air_flow is unset (any value, "powerful" included).
    if status.wind_shield_hit == NOT_SET:
        controls.append(LOUVERS)
        if status.air_flow == NOT_SET:
            controls.append(FAN)
    if mode not in AI_UNSUPPORTED_MODES:
        controls.append(AI_MODE)
    if effective not in AIR_FLOW_UNSUPPORTED_MODES:
        controls.append(AIR_FLOW)
    if effective not in SHIELD_HIT_UNSUPPORTED_MODES:
        controls.append(WIND_SHIELD_HIT)
    controls += [NANOEX, SILENCE_CONTROL, AIR_QUALITY_MONITOR]
    if mode == EoliaOperationMode.COMFORTABLE_DEHUMIDIFICATION:
        controls.append(DRY_HUMIDITY_TARGET)
    controls.append(DOUBLE_TEMP_ENABLED)
    return tuple(controls)
