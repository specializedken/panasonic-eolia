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


# --- ai_control enum (wire values) ------------------------------------------------------
# AI mode / ECONAVI are NOT independently toggleable in this API -- ECONAVI is a third
# state of the same field alongside AI-comfort-mode. See findings.md.
class EoliaAiControl(StrEnum):
    """Wire values of the `ai_control` field."""

    OFF = "off"
    COMFORTABLE = "comfortable"
    COMFORTABLE_ECONAVI = "comfortable_econavi"


# --- wind_direction_horizon enum (wire values) -------------------------------------------
class EoliaWindDirectionHorizon(StrEnum):
    """Wire values of the `wind_direction_horizon` field."""

    FRONT = "front"
    SPOT = "spot"
    WIDE = "wide"
    TO_LEFT = "to_left"
    NEARBY_LEFT = "nearby_left"
    NEARBY_RIGHT = "nearby_right"
    TO_RIGHT = "to_right"
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
# Temperature out of valid range for the target operation_mode (observed with
# temperature=0.0 carried over from a Stop-mode status into an active-mode write).
ERROR_CODE_TEMPERATURE_OUT_OF_RANGE = "E-21291-01712"
# Another client wrote to the device within the last ~2 minutes -- confirmed live to be
# triggered by the official app (even a no-op write on menu close, not just an actual
# value change). See tests/fixtures/live_captures/02's notes.
ERROR_CODE_DEVICE_LOCKED = "E-21291-01718"
# double_mode_temp.high/low must be at least 5 degrees apart.
ERROR_CODE_DOUBLE_TEMP_RANGE_TOO_NARROW = "E-21291-02009"

# --- Fixed control-request payload contract -------------------------------------------------
# Confirmed live 2026-09-23 by capturing a real PUT from the actual Eolia app: the body
# must NOT include applianceId (URL only) or humidity, and MUST include silence_control
# even though it has no readback in GET /status. See findings.md's "RESOLVED" section.
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
# on the same integer: 0=auto (server-side "auto" toggle in the app; while that toggle is
# on app-side, /status always reports 0 regardless of what value the API last wrote --
# the write isn't rejected or lost, it's just invisible/inert until auto is turned off in
# the app; no API field to toggle auto itself has been found), 1-5=fixed positions from
# "upper" to "straight down" (1, 3, and 5 individually confirmed; 2 and 4 inferred by
# pattern -- and confirmed as GOOD inferred values, since writing 2 directly worked and
# was confirmed in the app), 6=swing/oscillate (this one is NOT part of a linear position
# scale -- it's a distinct continuous-motion mode, but unlike auto, a fixed-position write
# (1-5) DOES immediately override it via the API alone). See
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
