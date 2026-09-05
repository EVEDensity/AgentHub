"""Deterministic, read-only utility tools for current facts.

These tools exist so the model never has to guess values that are available
from the local clock or a current public data source.  Weather uses Open-
Meteo's keyless geocoding and forecast endpoints; failures are returned
explicitly instead of being replaced with synthetic data.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx


LOCAL_TIMEZONE_ENV = "AGENTHUB_TIMEZONE"
WEATHER_LOCATION_ENV = "AGENTHUB_WEATHER_LOCATION"
UTILITY_TIMEOUT_SECONDS = 12.0

_WEATHER_CODES = {
    0: "晴",
    1: "大致晴",
    2: "局部多云",
    3: "阴",
    45: "雾",
    48: "冻雾",
    51: "毛毛雨",
    53: "毛毛雨",
    55: "强毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    80: "阵雨",
    81: "中阵雨",
    82: "强阵雨",
    95: "雷暴",
    96: "雷暴伴小冰雹",
    99: "雷暴伴大冰雹",
}

# Windows Python installations do not always ship the IANA tzdata package.
# These common fixed-offset zones keep basic CLI queries functional there;
# callers needing DST-aware or uncommon zones still get a clear error.
_FIXED_TIMEZONES = {
    "UTC": timezone.utc,
    "Etc/UTC": timezone.utc,
    "Asia/Shanghai": timezone(timedelta(hours=8), "Asia/Shanghai"),
    "Asia/Taipei": timezone(timedelta(hours=8), "Asia/Taipei"),
    "Asia/Tokyo": timezone(timedelta(hours=9), "Asia/Tokyo"),
    "Asia/Seoul": timezone(timedelta(hours=9), "Asia/Seoul"),
    "Asia/Singapore": timezone(timedelta(hours=8), "Asia/Singapore"),
}


def _timezone(name: str = "") -> tuple[tzinfo | None, str, str | None]:
    requested = (name or os.environ.get(LOCAL_TIMEZONE_ENV, "")).strip()
    if requested:
        try:
            return ZoneInfo(requested), requested, None
        except ZoneInfoNotFoundError:
            fixed = _FIXED_TIMEZONES.get(requested)
            if fixed is not None:
                return fixed, requested, None
            return None, requested, f"无法识别时区: {requested}"
    local = datetime.now().astimezone()
    return local.tzinfo if isinstance(local.tzinfo, ZoneInfo) else None, str(local.tzinfo), None


async def current_time_handler(timezone: str = "") -> dict[str, Any]:
    """Return the current local time without involving a language model."""
    zone, label, error = _timezone(timezone)
    if error:
        return {"success": False, "error": error}
    now = datetime.now(zone) if zone is not None else datetime.now().astimezone()
    return {
        "success": True,
        "result": {
            "iso": now.isoformat(),
            "formatted": now.strftime("%Y-%m-%d %H:%M:%S"),
            "timezone": label,
        },
        "metadata": {"source": "system-clock", "timezone": label},
    }


async def current_date_handler(timezone: str = "") -> dict[str, Any]:
    """Return the current calendar date from the local system clock."""
    zone, label, error = _timezone(timezone)
    if error:
        return {"success": False, "error": error}
    now = datetime.now(zone) if zone is not None else datetime.now().astimezone()
    return {
        "success": True,
        "result": {
            "iso": now.date().isoformat(),
            "formatted": now.strftime("%Y-%m-%d"),
            "weekday": now.strftime("%A"),
            "timezone": label,
        },
        "metadata": {"source": "system-clock", "timezone": label},
    }


async def _json_get(url: str, params: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=UTILITY_TIMEOUT_SECONDS) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
    return payload if isinstance(payload, dict) else {}


async def weather_handler(
    location: str = "",
    latitude: float | None = None,
    longitude: float | None = None,
) -> dict[str, Any]:
    """Fetch current weather for a named place or explicit coordinates."""
    location = (location or os.environ.get(WEATHER_LOCATION_ENV, "")).strip()
    try:
        lat = float(latitude) if latitude is not None else None
        lon = float(longitude) if longitude is not None else None
    except (TypeError, ValueError):
        return {"success": False, "error": "latitude 和 longitude 必须是数字"}
    if (lat is None) != (lon is None):
        return {"success": False, "error": "latitude 和 longitude 必须同时提供"}
    if lat is None and not location:
        return {"success": False, "error": "天气查询需要 location，或同时提供 latitude/longitude"}
    if lat is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return {"success": False, "error": "经纬度超出有效范围"}

    try:
        if lat is None:
            geo = await _json_get(
                "https://geocoding-api.open-meteo.com/v1/search",
                {"name": location, "count": 1, "language": "zh", "format": "json"},
            )
            matches = geo.get("results") or []
            if not matches:
                return {"success": False, "error": f"未找到地点: {location}"}
            match = matches[0]
            lat = float(match["latitude"])
            lon = float(match["longitude"])
            resolved_location = ", ".join(
                str(value) for value in (match.get("name"), match.get("country")) if value
            )
        else:
            resolved_location = location or f"{lat:.4f},{lon:.4f}"
        forecast = await _json_get(
            "https://api.open-meteo.com/v1/forecast",
            {
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
                "timezone": "auto",
            },
        )
        current = forecast.get("current") or {}
        units = forecast.get("current_units") or {}
        if not current:
            return {"success": False, "error": "天气服务未返回当前数据"}
        code = int(current.get("weather_code", -1))
        return {
            "success": True,
            "result": {
                "location": resolved_location,
                "time": current.get("time"),
                "condition": _WEATHER_CODES.get(code, f"天气代码 {code}"),
                "temperature": current.get("temperature_2m"),
                "temperature_unit": units.get("temperature_2m", "°C"),
                "apparent_temperature": current.get("apparent_temperature"),
                "humidity": current.get("relative_humidity_2m"),
                "wind_speed": current.get("wind_speed_10m"),
                "wind_speed_unit": units.get("wind_speed_10m", "km/h"),
            },
            "metadata": {"source": "open-meteo", "latitude": lat, "longitude": lon},
        }
    except httpx.TimeoutException:
        return {"success": False, "error": f"天气查询超时（{UTILITY_TIMEOUT_SECONDS:g}秒）"}
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        return {"success": False, "error": f"天气查询失败: {exc}"}
