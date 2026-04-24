from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


def _route(drone_id: int, start_lat: float) -> dict:
    return {
        "drone_id": drone_id,
        "route": [
            {"lat": start_lat, "lng": 41.9701},
            {"lat": start_lat + 0.0005, "lng": 41.9706},
        ],
    }


def test_mission_ws_simulation_frame_contains_multiple_drones():
    with TestClient(app) as client:
        with client.websocket_connect("/ws/telemetry/mission") as ws:
            ws.send_json(
                {
                    "protocol": "v1",
                    "mode": "simulation",
                    "routes": [_route(1, 45.0401), _route(2, 45.0411)],
                    "irm": 0.82,
                }
            )
            frame = ws.receive_json()

    assert frame["protocol"] == "v1"
    assert frame["source"] == "simulation"
    assert isinstance(frame["telemetry"], list)
    assert len(frame["telemetry"]) >= 2
    assert "fusion_by_drone" in frame


def test_mission_ws_live_frame_contains_fusion_snapshot():
    async def _fake_loop(drone_id: int):
        while True:
            yield {
                "drone_id": drone_id,
                "lat": 45.04 + drone_id * 0.0001,
                "lng": 41.97 + drone_id * 0.0001,
                "alt": 30.0,
                "battery": 91,
                "heading": 120,
                "status": "ACTIVE",
                "groundspeed": 8.5,
            }

    with (
        patch(
            "app.api.routers.telemetry.mavlink_service.read_telemetry_loop",
            side_effect=lambda did: _fake_loop(did),
        ),
        patch(
            "app.api.routers.telemetry.get_fusion_snapshot",
            side_effect=lambda did: {"fused_threat_level": 0.7} if did == 1 else None,
        ),
        TestClient(app) as client,
    ):
        with client.websocket_connect("/ws/telemetry/mission") as ws:
            ws.send_json(
                {
                    "protocol": "v1",
                    "mode": "live",
                    "routes": [
                        {"drone_id": 1, "route": []},
                        {"drone_id": 2, "route": []},
                    ],
                }
            )
            frame = ws.receive_json()

    assert frame["protocol"] == "v1"
    assert frame["source"] == "live"
    assert len(frame["telemetry"]) >= 2
    assert frame["fusion_by_drone"].get("1", {}).get("fused_threat_level") == 0.7
