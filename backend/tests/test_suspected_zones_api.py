from __future__ import annotations

from fastapi.testclient import TestClient
from types import SimpleNamespace

from app.api.deps import require_operator
from app.main import app
from app.services.mission_fusion_runtime import clear_fusion_context, get_dynamic_zones_snapshot


async def _fake_operator():
    return SimpleNamespace(id=1, role="operator", is_active=True)


def setup_function():
    clear_fusion_context()
    app.dependency_overrides[require_operator] = _fake_operator


def teardown_function():
    clear_fusion_context()
    app.dependency_overrides.clear()


def test_create_suspected_zone_and_patch_state():
    with TestClient(app) as client:
        create = client.post(
            "/api/v1/risk-zones/suspected",
            json={
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[41.97, 45.04], [41.98, 45.04], [41.98, 45.05], [41.97, 45.05], [41.97, 45.04]]],
                },
                "source": "operator",
                "ttl_sec": 30,
                "note": "manual hint",
            },
        )
        assert create.status_code == 200
        body = create.json()
        assert body["state"] == "DRAWN"
        zone_id = body["zone_id"]

        patch_resp = client.patch(
            f"/api/v1/risk-zones/{zone_id}/state",
            json={"state": "REJECTED"},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["state"] == "REJECTED"

    snap = get_dynamic_zones_snapshot()
    assert any(z["zone_id"] == zone_id and z["state"] == "REJECTED" for z in snap)
