from fastapi.testclient import TestClient

from geoassist.api.app import AppDependencies, create_app
from geoassist.api.readiness import ReadinessProbe
from geoassist.config import Settings
from geoassist.rag.retriever import RetrievalResult
from geoassist.teams.outgoing_webhook import OutgoingWebhookAdapter


class FakeRetriever:
    def retrieve(self, question, app_filter=None):
        return RetrievalResult((), False, "no_candidates", {"dense": 0, "lexical": 0})


def make_app(enabled=True):
    settings = Settings(
        _env_file=None,
        teams_security_token="secret",
        llm_api_key="k",
        llm_data_policy="external_approved",
        debug_retrieval=enabled,
        debug_token="debug-token",
    )
    deps = AppDependencies(
        settings, OutgoingWebhookAdapter("secret", "Bot"), None, FakeRetriever(), None, None
    )
    return create_app(settings, probe=ReadinessProbe({}), dependencies=deps)


def test_debug_requires_token():
    response = TestClient(make_app()).post("/internal/retrieval", json={"question": "x"})
    assert response.status_code == 401


def test_debug_returns_trace_with_token():
    response = TestClient(make_app()).post(
        "/internal/retrieval",
        json={"question": "x"},
        headers={"X-Debug-Token": "debug-token"},
    )
    assert response.status_code == 200
    assert response.json()["trace"]["dense"] == 0
    assert response.json()["chunks"] == []


def test_debug_disabled_returns_404():
    response = TestClient(make_app(enabled=False)).post(
        "/internal/retrieval",
        json={"question": "x"},
        headers={"X-Debug-Token": "debug-token"},
    )
    assert response.status_code == 404
