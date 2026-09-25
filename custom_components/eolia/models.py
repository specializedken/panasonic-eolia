"""Data models for the Eolia integration.

Field sets and types are taken from the real captured GET /devices, GET /status, and PUT
/status request/response JSON documented in docs/findings.md -- not guessed from the decompiled
Java models, which (per docs/findings.md) include several fields never observed on this device
and whose capability-gating logic isn't fully understood. Everything here is read
defensively (`.get()` with sensible defaults) since a different device may expose a
different field set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Self

from .const import (
    CONTROL_REQUEST_FIELDS,
    CUSTOM_SETTINGS_REQUEST_FIELDS,
    OUTSIDE_TEMP_UNAVAILABLE_SENTINEL,
)


@dataclass(frozen=True, kw_only=True, slots=True)
class EoliaDevice:
    """A single entry from GET /devices' `ac_list`."""

    appliance_id: str
    nickname: str
    product_code: str
    product_name: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Build from one element of the `ac_list` array."""
        return cls(
            appliance_id=data["appliance_id"],
            nickname=data.get("nickname") or data.get("product_code", "Eolia"),
            product_code=data.get("product_code", ""),
            product_name=data.get("product_name", ""),
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class EoliaStatus:
    """Parsed GET/PUT .../status response.

    Does NOT include `silence_control` -- that field is write-only (present in every real
    captured PUT request but absent from every captured GET/PUT response), so it can't be
    read back here. The coordinator tracks its last-sent value separately; see
    coordinator.py.
    """

    appliance_id: str
    operation_status: bool
    operation_mode: str
    temperature: float
    wind_volume: int
    wind_direction: int
    wind_direction_horizon: str
    air_flow: str
    wind_shield_hit: str
    airquality: bool
    nanoex: bool
    ai_control: str
    timer_value: int

    # Read-only / informational fields -- not sent back on control writes.
    inside_temp: float | None
    inside_humidity: int | None
    outside_temp: float | None
    aq_name: str | None
    aq_value: int | None
    device_errstatus: bool | None
    operation_priority: bool | None
    operation_token: str | None  # only present on PUT responses, not GET
    # Only reported while operation_mode is ComfortableDehumidification (Dry): the target.
    # Never sent back via to_control_fields() -- the coordinator injects it for Dry only.
    humidity: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Build from a GET or PUT .../status response body."""
        return cls(
            appliance_id=data["appliance_id"],
            operation_status=bool(data.get("operation_status", False)),
            operation_mode=data.get("operation_mode", "Stop"),
            temperature=float(data.get("temperature", 0.0)),
            wind_volume=int(data.get("wind_volume", 0)),
            wind_direction=int(data.get("wind_direction", 0)),
            wind_direction_horizon=data.get("wind_direction_horizon", "auto"),
            air_flow=data.get("air_flow", "not_set"),
            wind_shield_hit=data.get("wind_shield_hit", "not_set"),
            airquality=bool(data.get("airquality", False)),
            nanoex=bool(data.get("nanoex", False)),
            ai_control=data.get("ai_control", "off"),
            timer_value=int(data.get("timer_value", 0)),
            inside_temp=_optional_float(data.get("inside_temp")),
            inside_humidity=_optional_int(data.get("inside_humidity")),
            outside_temp=_outside_temp(data.get("outside_temp")),
            aq_name=data.get("aq_name"),
            aq_value=_optional_int(data.get("aq_value")),
            device_errstatus=data.get("device_errstatus"),
            operation_priority=data.get("operation_priority"),
            operation_token=data.get("operation_token"),
            humidity=_optional_int(data.get("humidity")),
        )

    def to_control_fields(self) -> dict[str, Any]:
        """Project this status onto the fixed control-request field set.

        Deliberately excludes `applianceId` (URL only) and `humidity` (confirmed absent
        from the real captured PUT request for this device) and `silence_control` (not
        part of this model at all -- the coordinator injects it from its own cache). See
        docs/findings.md's "RESOLVED" section and const.CONTROL_REQUEST_FIELDS.
        """
        payload: dict[str, Any] = {
            "ai_control": self.ai_control,
            "air_flow": self.air_flow,
            "airquality": self.airquality,
            "nanoex": self.nanoex,
            "operation_mode": self.operation_mode,
            "operation_status": self.operation_status,
            "temperature": self.temperature,
            "timer_value": self.timer_value,
            "wind_direction": self.wind_direction,
            "wind_volume": self.wind_volume,
            "wind_direction_horizon": self.wind_direction_horizon,
            "wind_shield_hit": self.wind_shield_hit,
        }
        # Defensive: keep this in sync with CONTROL_REQUEST_FIELDS minus silence_control.
        assert set(payload) == set(CONTROL_REQUEST_FIELDS) - {"silence_control"}
        return payload


@dataclass(frozen=True, kw_only=True, slots=True)
class EoliaDoubleModeTemp:
    """The `double_mode_temp` sub-object of a .../customsettings response."""

    status: bool
    high: int
    low: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            status=bool(data.get("status", False)),
            high=int(data.get("high", 28)),
            low=int(data.get("low", 23)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "high": self.high, "low": self.low}


@dataclass(frozen=True, kw_only=True, slots=True)
class EoliaCustomSettings:
    """Parsed GET/PUT .../customsettings response.

    A separate resource from EoliaStatus -- KeepMode's low/high range lives here, not in
    /status (confirmed live 2026-09-23, see docs/findings.md). Unlike EoliaStatus, the GET
    response has no `appliance_id` field at all, so this model doesn't carry one either;
    the coordinator keys it by appliance_id externally, same as it does for silence_control.
    """

    double_mode_temp: EoliaDoubleModeTemp
    peak_cut: int

    # Read-only / informational fields -- not sent back on control writes.
    operation_priority: bool | None
    device_errstatus: bool | None
    operation_token: str | None  # only present on PUT responses, not GET (same as EoliaStatus)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            double_mode_temp=EoliaDoubleModeTemp.from_dict(data.get("double_mode_temp", {})),
            peak_cut=int(data.get("peak_cut", 100)),
            operation_priority=data.get("operation_priority"),
            device_errstatus=data.get("device_errstatus"),
            operation_token=data.get("operation_token"),
        )

    def to_control_fields(self) -> dict[str, Any]:
        """Project onto the fixed .../customsettings control-request field set.

        Deliberately excludes `appliance_id` (URL only, and not even present in the GET
        response body to begin with). See const.CUSTOM_SETTINGS_REQUEST_FIELDS.
        """
        payload: dict[str, Any] = {
            "double_mode_temp": self.double_mode_temp.to_dict(),
            "peak_cut": self.peak_cut,
        }
        assert set(payload) == set(CUSTOM_SETTINGS_REQUEST_FIELDS)
        return payload


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _outside_temp(value: Any) -> float | None:
    """outside_temp uses OUTSIDE_TEMP_UNAVAILABLE_SENTINEL (999.0) as a sentinel for "no
    reading available" rather than omitting the field or sending null -- confirmed live
    repeatedly (see const.py). Treat it the same as an absent value so it never shows up
    as a literal 999C reading in HA.
    """
    temp = _optional_float(value)
    return None if temp == OUTSIDE_TEMP_UNAVAILABLE_SENTINEL else temp
