from fastapi.testclient import TestClient

from geoassist.api.app import create_app
from geoassist.api.readiness import ReadinessProbe


def test_liveness_is_independent_of_dependencies():
    app = create_app(probe=ReadinessProbe({"corpus": lambda: False}))
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_each_check():
    probe = ReadinessProbe({"corpus": lambda: False, "models": lambda: True})
    response = TestClient(create_app(probe=probe)).get("/ready")
    assert response.status_code == 503
    assert response.json() == {"ready": False, "checks": {"corpus": False, "models": True}}


def test_readiness_returns_200_when_all_checks_pass():
    probe = ReadinessProbe({"corpus": lambda: True, "models": lambda: True})
    response = TestClient(create_app(probe=probe)).get("/ready")
    assert response.status_code == 200
    assert response.json()["ready"] is True
