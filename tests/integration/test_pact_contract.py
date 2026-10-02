"""Consumer-driven contract between the web UI (consumer) and API (provider), expressed as a JSON-schema pact
that runs without a broker; replace with pact-python in CI when a Pact Broker is available."""
from fastapi.testclient import TestClient
from fleetpulse.api.app import create_app
from fleetpulse.api.auth import issue_dev_token

UI_EXPECTS_VEHICLE = {"vin": str, "risk": float, "factors": list, "fuel": str, "lat": float, "lon": float}
UI_EXPECTS_ALERT = {"type": str, "vin": str, "severity": int, "ts": str}


def test_ui_contract_vehicles_and_alerts():
    c = TestClient(create_app())
    h = {"Authorization": f"Bearer {issue_dev_token('u', 't00', ['fleet_manager'])}"}
    v = c.get("/v1/vehicles?limit=3", headers=h).json()
    assert set(v) == {"items", "next_cursor"}
    for item in v["items"]:
        for k, t in UI_EXPECTS_VEHICLE.items(): assert isinstance(item[k], t), k
    for item in c.get("/v1/alerts?limit=3", headers=h).json()["items"]:
        for k, t in UI_EXPECTS_ALERT.items(): assert isinstance(item[k], t), k
