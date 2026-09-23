"""Constants for the Eolia integration.

All values here are taken directly from findings.md, which documents the confirmed
(static analysis + live captured traffic) contract of Panasonic's Eolia cloud API.
Do not change any wire-format value (Auth0 params, API paths, enum strings) without
re-confirming against findings.md or fresh live traffic -- these are not guesses.
"""

from __future__ import annotations

from enum import StrEnum

DOMAIN = "eolia"

# --- Auth0 --------------------------------------------------------------------------
# Native-app client registration; redirect_uri is fixed and only accepts this exact
# custom scheme (confirmed live 2026-09-23 -- any other redirect_uri gets an immediate
# "Callback URL mismatch" error). See findings.md's "The one hard external constraint".
AUTH0_DOMAIN = "auth.digital.panasonic.com"
AUTH0_CLIENT_ID = "JpNCoLeXs4rPMhWmnOjbOxat7MWTZEgr"
AUTH0_AUDIENCE = "https://club.panasonic.jp/JpNCoLeXs4rPMhWmnOjbOxat7MWTZEgr/api/v1/"
AUTH0_SCOPE = "openid offline_access eolia.control"
AUTH0_REDIRECT_URI = (
    "panasonic-eolia://auth.digital.panasonic.com/android/com.panasonic.SmartRAC/callback"
)
AUTH0_AUTHORIZE_URL = f"https://{AUTH0_DOMAIN}/authorize"
AUTH0_TOKEN_URL = f"https://{AUTH0_DOMAIN}/oauth/token"
AUTH0_USERINFO_URL = f"https://{AUTH0_DOMAIN}/userinfo"

# --- Eolia API ------------------------------------------------------------------------
API_BASE_URL = "https://app.rac.apws.panasonic.com/eolia/v6"

# Server enforces a +/-5 minute clock-skew check against this header, formatted with no
# timezone offset -- it must be JST regardless of the HA host's configured timezone, or
# every request fails with E-21291-00002. Confirmed live 2026-09-23.
EOLIA_DATE_TIMEZONE = "Asia/Tokyo"
EOLIA_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"

DEFAULT_SCAN_INTERVAL_SECONDS = 60  # unvalidated guess -- findings.md documents no rate limit

# outside_temp uses this as a sentinel for "no reading available" rather than omitting
# the field or sending null -- live-confirmed 2026-09-23 (repeatedly): shows up right
# after power-on before the outdoor sensor's first real reading arrives, and again
# immediately on power-off. models.py treats it the same as an absent value.
OUTSIDE_TEMP_UNAVAILABLE_SENTINEL = 999.0

# Refresh the access token once less than this much time remains before its confirmed
# expiry (access tokens are issued with a 14-day lifetime).
TOKEN_REFRESH_MARGIN_SECONDS = 24 * 60 * 60

# --- Config entry data keys -----------------------------------------------------------
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_EXPIRES_AT = "expires_at"  # computed epoch seconds, not the raw expires_in


# --- operation_mode enum (wire values) -------------------------------------------------
# Full table + Japanese UI labels documented in findings.md's "operation_mode -- the
# 'Dry vs Cool & Dehumidify' answer" section, reverse engineered from s8/k.java.
class EoliaOperationMode(StrEnum):
    """Wire values of the `operation_mode` field."""

    AUTO = "Auto"
    COOLING = "Cooling"
    HEATING = "Heating"
    KEEP_HEATING = "KeepHeating"
    BLAST = "Blast"
    DEHUMIDIFYING = "Dehumidifying"
    COOL_DEHUMIDIFYING = "CoolDehumidifying"
    COMFORTABLE_DEHUMIDIFICATION = "ComfortableDehumidification"
    CLOTHES_DRYER = "ClothesDryer"
    MOIST_COOLING = "MoistCooling"
    AUTO_TEMP_CONTROL = "AutoTempControl"
    KEEP_MODE = "KeepMode"
    SMELL_CARE = "SmellCare"
    SMELL_CARE_SPOT = "SmellCareSpot"
    NANOEX_CLEANING = "NanoexCleaning"
    CLEANING = "Cleaning"
    STOP = "Stop"
    OTHER = "Other"


# Short, human-readable descriptions of what each operation_mode actually does --
# requested 2026-09-23 since the cooling/dehumidify family in particular is genuinely
# confusing (multiple modes that all sound like "cooling but drier"). The
# Cooling/CoolDehumidifying/ComfortableDehumidification/MoistCooling/ClothesDryer/Blast
# descriptions are sourced from Panasonic's own published glossary and feature pages
# (see URLs below) and line up closely with what was independently confirmed live this
# session -- notably, the official 快適除湿 (Comfortable Dehumidify) page states its
# humidity range as 50-60%, exactly matching the {50, 55, 60} bisected live in
# tests/fixtures/live_captures/20-24, and 冷房除湿's documented 16-30C temperature range
# matches what CoolDehumidifying accepted live. The remaining modes (Auto, Heating,
# KeepHeating, Dehumidifying, AutoTempControl, KeepMode, SmellCare/SmellCareSpot,
# NanoexCleaning, Cleaning, Stop, Other) are not part of that research pass -- their
# descriptions are either self-evident or carried over from findings.md's original
# decompiled Japanese-label table, not independently verified against official docs.
# Sources:
#   https://panasonic.jp/aircon/glossary/cooling.html
#   https://panasonic.jp/aircon/feature/dehumidification.html
#   https://jpn.faq.panasonic.com/app/answers/detail/a_id/9750 (clothes drying)
OPERATION_MODE_DESCRIPTIONS: dict[str, str] = {
    "Auto": "Automatically picks cooling, heating, or fan based on conditions.",
    "Cooling": "Standard cooling. Targets a set temperature (16-30C).",
    "Heating": "Standard heating. Targets a set temperature.",
    "KeepHeating": (
        "Heating with the fan kept running continuously (no warm-up standby pause), "
        "to avoid a draft of cool air while the unit is heating up."
    ),
    "Blast": (
        "Fan only -- circulates air without heating, cooling, or dehumidifying. "
        "No temperature control."
    ),
    "Dehumidifying": (
        "Generic dehumidify wire value. Never confirmed as an actual app-reachable "
        "option on this device -- the app's own 'dehumidification' menu item maps to "
        "ComfortableDehumidification instead."
    ),
    "CoolDehumidifying": (
        "\"Cool & Dehumidify\" (冷房除湿). Lowers BOTH temperature and "
        "humidity -- a real target temperature (16-30C), same as Cooling but drier. "
        "Colder and stronger than ComfortableDehumidification."
    ),
    "ComfortableDehumidification": (
        "\"Dry\" / Comfortable Dehumidify (快適除湿). Lowers humidity "
        "ONLY, with minimal cooling effect (partial heat-exchanger control keeps the "
        "room from getting cold). Targets a humidity level (50-60%, 5% steps) instead "
        "of a temperature -- temperature is fixed, not user-settable."
    ),
    "ClothesDryer": (
        "Clothes drying (衣類乾燥). Strong dehumidify + fan combo for "
        "drying indoor-hung laundry; nanoeX also suppresses damp-laundry odor. No "
        "temperature or fan-speed control (both fixed/automatic). Panasonic recommends "
        "using it only when the room is unoccupied."
    ),
    "MoistCooling": (
        "\"Moist Cooling\" (しっとり冷房). Cools while "
        "deliberately keeping humidity HIGHER than plain cooling would, to avoid the "
        "dry/cold feeling of standard AC -- energy-saving too. Real target temperature "
        "(16-30C) -- the opposite intent of the dehumidify-family modes above."
    ),
    "AutoTempControl": (
        "\"Leave it to us\" auto temperature control (おまかせ温"
        "度制御). AI-adjusted target temperature using outdoor-temperature "
        "correction; not independently researched against official docs."
    ),
    "KeepMode": (
        "\"Double temperature setting\" (ダブル温度設定). "
        "Maintains room temperature within a low/high band rather than one target. "
        "The actual range lives on a separate API resource (.../customsettings, not "
        "/status) -- see number.eolia_double_temp_low/_high."
    ),
    "SmellCare": "Odor-care (においケア) -- deodorizing mode.",
    "SmellCareSpot": (
        "Targeted odor-care (においケア ねらって"
        "脱臭) -- deodorizes a specific spot/area rather than the whole room."
    ),
    "NanoexCleaning": (
        "\"Away clean\" (おでかけクリーン) -- a nanoeX "
        "self-cleaning cycle, meant to run while nobody's home."
    ),
    "Cleaning": "Self-clean (おそうじ) -- internal cleaning cycle.",
    "Stop": "Power off.",
    "Other": "Unknown/unrecognized mode value -- fallback display only, never selectable.",
}


# --- ai_control enum (wire values) ------------------------------------------------------
# AI mode / ECONAVI are NOT independently toggleable in this API -- ECONAVI is a third
# state of the same field alongside AI-comfort-mode. See findings.md.
class EoliaAiControl(StrEnum):
    """Wire values of the `ai_control` field."""

    OFF = "off"
    COMFORTABLE = "comfortable"
    COMFORTABLE_ECONAVI = "comfortable_econavi"


# --- wind_direction_horizon enum (wire values) -------------------------------------------
# All 8 values live-confirmed against the app 2026-09-23 (see
# tests/fixtures/live_captures/27-33). Unlike wind_direction's numeric auto (which masks
# the underlying stored value and always reports 0 while active), this field's "auto"
# round-trips honestly in /status with no masking behavior observed.
class EoliaWindDirectionHorizon(StrEnum):
    """Wire values of the `wind_direction_horizon` field."""

    FRONT = "front"  # default/straight-ahead
    SPOT = "spot"  # focused airflow converging on one point
    WIDE = "wide"  # spread/diverging airflow, opposite of SPOT
    TO_LEFT = "to_left"  # fixed, pointing left
    NEARBY_LEFT = "nearby_left"  # fixed, partial-left (between FRONT and TO_LEFT)
    NEARBY_RIGHT = "nearby_right"  # fixed, partial-right (mirror of NEARBY_LEFT)
    TO_RIGHT = "to_right"  # fixed, pointing right
    AUTO = "auto"


# --- air_flow enum (wire values) ----------------------------------------------------------
# All 4 values live-confirmed against the app 2026-09-23 (see
# tests/fixtures/live_captures/10-12). Independent of wind_volume (fan speed) -- both can
# be set at the same time, air_flow is a character/boost mode layered on top.
class EoliaAirFlow(StrEnum):
    """Wire values of the `air_flow` field."""

    NOT_SET = "not_set"
    QUIET = "quiet"
    POWERFUL = "powerful"
    LONG = "long"  # extended-reach airflow, noticeably more air volume (nanoeX-long)


# --- wind_shield_hit enum (wire values) ---------------------------------------------------
# Both non-default values live-confirmed against the app 2026-09-23 (see
# tests/fixtures/live_captures/13-14). Human-detection based airflow avoidance/targeting.
class EoliaWindShieldHit(StrEnum):
    """Wire values of the `wind_shield_hit` field."""

    NOT_SET = "not_set"
    SHIELD = "shield"  # avoid blowing directly at detected people
    HIT = "hit"  # deliberately blow at detected people


# --- Known Eolia error codes ---------------------------------------------------------------
# See findings.md for detail. Everything else should be logged verbatim rather than
# guessed at.
ERROR_CODE_CLOCK_SKEW = "E-21291-00002"
ERROR_CODE_GENERIC_APPLICATION_ERROR = "E-21291-00007"
# Generic "system error" -- seen from several untested /poc/.../eco/* and
# /powermonitor/settings endpoints; not conclusively "unsupported", could just need query
# params that weren't guessed. See findings.md's "Power/eco history" section.
ERROR_CODE_SYSTEM_ERROR = "E-21291-00000"
# Temperature out of valid range for the target operation_mode (observed with
# temperature=0.0 carried over from a Stop-mode status into an active-mode write, and
# also with a real nonzero temperature sent while switching into a mode -- like
# ComfortableDehumidification/ClothesDryer -- that requires 0.0 instead).
ERROR_CODE_TEMPERATURE_OUT_OF_RANGE = "E-21291-01712"
# Seen once, switching directly to plain Dehumidifying (never confirmed as a real
# app-reachable mode on this device -- see OPERATION_MODE_DESCRIPTIONS). Same generic
# "an application error occurred" message as 00007/01712; not distinguished further.
ERROR_CODE_UNKNOWN_01711 = "E-21291-01711"
# "Controlled by another device, cannot change for 2 minutes" -- the literal message,
# but RESOLVED 2026-09-23 to not really be about "another device": a controlled A/B
# test via the CLI confirmed the real mechanism is operation_token continuity. Every
# successful write returns a fresh operation_token; echoing it back on the very next
# write (even seconds later) avoids this lockout entirely, while an otherwise-identical
# write without it reliably triggers it. coordinator.py now caches and echoes this
# automatically (_operation_token_cache). See
# tests/fixtures/live_captures/38_operation_token_confirmed_fix.json for the full A/B
# test, and 02/04/37 for how the "another device" theory was arrived at and then
# superseded.
ERROR_CODE_DEVICE_LOCKED = "E-21291-01718"
# double_mode_temp.high/low must be at least 5 degrees apart.
ERROR_CODE_DOUBLE_TEMP_RANGE_TOO_NARROW = "E-21291-02009"

# --- Fixed control-request payload contract -------------------------------------------------
# Confirmed live 2026-09-23 by capturing a real PUT from the actual Eolia app: the body
# must NOT include applianceId (URL only) or humidity, and MUST include silence_control
# even though it has no readback in GET /status. See findings.md's "RESOLVED" section.
# CORRECTION, later the same day: "must not include humidity" turned out to be
# mode-specific, not universal -- ComfortableDehumidification is the one exception that
# actually REQUIRES humidity (see DRY_MODE_HUMIDITY_RANGE below). humidity stays out of
# this fixed field list since it's not applicable to any other mode tested.
CONTROL_REQUEST_FIELDS = (
    "ai_control",
    "air_flow",
    "airquality",
    "nanoex",
    "operation_mode",
    "silence_control",
    "operation_status",
    "temperature",
    "timer_value",
    "wind_direction",
    "wind_volume",
    "wind_direction_horizon",
    "wind_shield_hit",
)

# wind_volume (fan speed): live-confirmed 2026-09-23 against the app -- 0=auto, 1=lowest
# ("minimal"), 5=highest ("max"). 2-4 weren't individually confirmed but fit the obvious
# linear pattern between confirmed neighbors. See tests/fixtures/live_captures/08-09.
WIND_VOLUME_LEVELS = (0, 1, 2, 3, 4, 5)  # 0 = auto

# wind_direction (vertical louver): live-confirmed 2026-09-23. Two independent axes ride
# on the same integer: 0=auto, 1-5=fixed positions from "upper" to "straight down" (1, 3,
# and 5 individually confirmed; 2 and 4 inferred by pattern -- and confirmed as GOOD
# inferred values, since writing 2 directly worked and was confirmed in the app),
# 6=swing/oscillate (NOT part of the linear position scale -- a distinct
# continuous-motion mode). Entering/leaving each state via the API is ASYMMETRIC, both
# confirmed live: writing 0 always works and puts the unit into auto (confirmed by
# writing it while in a fixed position and seeing the app's auto toggle turn on);
# writing 1-5 works when starting from a fixed position OR from swing (6) -- a
# fixed-position write immediately overrides swing. But writing 1-5 while the unit is
# ALREADY in auto (0) is silently ignored: /status keeps reporting 0 regardless of what
# was sent, and the write is not lost (it becomes visible once auto is turned off), just
# inert until then. No API field to turn auto OFF has been found -- only the app's own
# vertical-auto toggle does that; the API can freely enter auto but not leave it. See
# tests/fixtures/live_captures/07 and 15 for the full investigation.
WIND_DIRECTION_LEVELS = (0, 1, 2, 3, 4, 5, 6)
WIND_DIRECTION_SWING = 6

# Temperature step is unconfirmed (the single live capture, 20.0, doesn't disambiguate
# 0.5 vs 1.0 steps). Default to whole degrees; validate on first live control test.
PROVISIONAL_TEMPERATURE_STEP = 1.0

# --- KeepMode ("double temperature setting") -- /customsettings -----------------------
# Separate resource from /status -- see findings.md's "KeepMode / double temperature
# setting" section. appliance_id is excluded from the PUT body the same way as /status
# (id only in the URL); unlike /status, this resource's GET response has no appliance_id
# field at all.
CUSTOM_SETTINGS_REQUEST_FIELDS = ("double_mode_temp", "peak_cut")

# App-enforced bounds (MyAirconSettingActivity.X() falls back to the last-known value
# outside these ranges) -- not yet confirmed as server-enforced, just what the app itself
# allows the user to pick. See findings.md.
DOUBLE_MODE_TEMP_HIGH_RANGE = (21, 30)
DOUBLE_MODE_TEMP_LOW_RANGE = (16, 25)

# --- ComfortableDehumidification ("Dry") mode's humidity target -----------------------
# Major finding, live-confirmed 2026-09-23: unlike every other operation_mode tested,
# ComfortableDehumidification requires `humidity` in the PUT body (the general contract
# elsewhere deliberately EXCLUDES it -- see CONTROL_REQUEST_FIELDS's docstring and
# findings.md's "RESOLVED" section) and requires `temperature=0.0` (a real target
# temperature, e.g. 24.0, is rejected with E-21291-01712 -- this mode doesn't target a
# temperature, it targets humidity instead). Without `humidity` present at all, every
# attempt failed with the generic E-21291-00007. Server-validated range, confirmed by
# direct trial: 50/55/60 all accepted, 40/65/70/85 all rejected (same generic error, no
# distinguishing code) -- so the real range is 50-60 inclusive in 5% steps (NOT up to 80
# or 100 as naively guessed from the AC's cooling-side conventions). Like
# `silence_control`, this field has no GET readback at all -- EoliaStatus doesn't model
# it, coordinator.py would need its own local cache the same way it does for
# silence_control if this becomes a real HA-controllable feature (e.g. via
# ClimateEntityFeature.TARGET_HUMIDITY) rather than just a documented finding. See
# tests/fixtures/live_captures/19-24 for the full trial-and-error sequence.
DRY_MODE_HUMIDITY_RANGE = (50, 60)
DRY_MODE_HUMIDITY_STEP = 5
