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
class EoliaAirFlow(StrEnum):
    """Wire values of the `air_flow` field (quiet/powerful/nanoeX-long)."""

    NOT_SET = "not_set"
    QUIET = "quiet"
    POWERFUL = "powerful"
    LONG = "long"


# --- wind_shield_hit enum (wire values) ---------------------------------------------------
class EoliaWindShieldHit(StrEnum):
    """Wire values of the `wind_shield_hit` field."""

    NOT_SET = "not_set"
    SHIELD = "shield"
    HIT = "hit"


# --- Known Eolia error codes ---------------------------------------------------------------
# Only these two have been observed and diagnosed live -- see findings.md. Everything
# else should be logged verbatim rather than guessed at.
ERROR_CODE_CLOCK_SKEW = "E-21291-00002"
ERROR_CODE_GENERIC_APPLICATION_ERROR = "E-21291-00007"

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

# wind_volume (fan speed) / wind_direction (vertical louver) level ranges are NOT
# confirmed anywhere in findings.md -- only that they are ints, and 3/3 was observed in
# the one live capture. This is a provisional guess; validate against the real app during
# the manual smoke test and correct findings.md + this constant if wrong.
PROVISIONAL_WIND_VOLUME_LEVELS = (0, 1, 2, 3, 4, 5)  # 0 = auto
PROVISIONAL_WIND_DIRECTION_LEVELS = (0, 1, 2, 3, 4, 5)  # 0 = auto

# Temperature step is unconfirmed (the single live capture, 20.0, doesn't disambiguate
# 0.5 vs 1.0 steps). Default to whole degrees; validate on first live control test.
PROVISIONAL_TEMPERATURE_STEP = 1.0
