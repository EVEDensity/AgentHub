from __future__ import annotations

import asyncio

from app.services.tools.utility_tools import (
    current_date_handler,
    current_time_handler,
    weather_handler,
)


def test_current_time_uses_requested_timezone() -> None:
    result = asyncio.run(current_time_handler("Asia/Shanghai"))
    assert result["success"] is True
    assert result["result"]["timezone"] == "Asia/Shanghai"
    assert len(result["result"]["formatted"]) == 19


def test_current_time_rejects_unknown_timezone() -> None:
    result = asyncio.run(current_time_handler("Not/ARealZone"))
    assert result["success"] is False
    assert "时区" in result["error"]


def test_current_date_returns_system_date() -> None:
    result = asyncio.run(current_date_handler("Asia/Shanghai"))
    assert result["success"] is True
    assert len(result["result"]["iso"]) == 10
    assert result["metadata"]["source"] == "system-clock"


def test_weather_requires_location_or_coordinates(monkeypatch) -> None:
    async def no_local_location():
        return None

    monkeypatch.setattr(
        "app.services.tools.utility_tools._discover_local_location",
        no_local_location,
    )
    result = asyncio.run(weather_handler())
    assert result["success"] is False
    assert "location" in result["error"]


def test_weather_resolves_location_and_returns_current_data(monkeypatch) -> None:
    responses = [
        {"results": [{"name": "上海", "country": "中国", "latitude": 31.23, "longitude": 121.47}]},
        {
            "current": {
                "time": "2026-09-06T10:00",
                "temperature_2m": 27.5,
                "relative_humidity_2m": 70,
                "apparent_temperature": 29.0,
                "weather_code": 1,
                "wind_speed_10m": 12.0,
            },
            "current_units": {"temperature_2m": "°C", "wind_speed_10m": "km/h"},
        },
    ]

    async def fake_json_get(url, params):
        return responses.pop(0)

    monkeypatch.setattr(
        "app.services.tools.utility_tools._json_get", fake_json_get
    )
    result = asyncio.run(weather_handler(location="上海"))
    assert result["success"] is True
    assert result["result"]["condition"] == "大致晴"
    assert result["result"]["temperature"] == 27.5
    assert result["metadata"]["source"] == "open-meteo"


def test_weather_auto_discovers_local_location(monkeypatch) -> None:
    response = {
        "current": {
            "time": "2026-09-06T10:00",
            "temperature_2m": 27.5,
            "relative_humidity_2m": 70,
            "apparent_temperature": 29.0,
            "weather_code": 61,
            "wind_speed_10m": 12.0,
        },
        "current_units": {"temperature_2m": "°C", "wind_speed_10m": "km/h"},
    }

    async def fake_json_get(url, params):
        return response

    monkeypatch.setattr("app.services.tools.utility_tools._json_get", fake_json_get)
    async def fake_discover():
        return {"latitude": 31.23, "longitude": 121.47, "location": "上海, 中国", "source": "public-ip"}

    monkeypatch.setattr("app.services.tools.utility_tools._discover_local_location", fake_discover)
    result = asyncio.run(weather_handler())
    assert result["success"] is True
    assert result["result"]["location"] == "上海, 中国"
    assert result["metadata"]["source"] == "open-meteo+ip-geolocation"
    assert result["metadata"]["locationApproximate"] is True


def test_weather_uses_local_timezone_city_before_network(monkeypatch) -> None:
    async def network_unavailable(url, params):
        raise httpx.ConnectError("offline")

    import httpx
    monkeypatch.setattr("app.services.tools.utility_tools._json_get", network_unavailable)
    monkeypatch.setattr(
        "app.services.tools.utility_tools.datetime",
        type("Clock", (), {"now": staticmethod(lambda: type("Now", (), {
            "astimezone": staticmethod(lambda: type("Zone", (), {"tzname": lambda self: "China Standard Time"})())
        })())}),
    )
    result = asyncio.run(
        __import__("app.services.tools.utility_tools", fromlist=["_discover_local_location"])
        ._discover_local_location()
    )
    assert result == {"location": "上海", "source": "local-timezone"}
