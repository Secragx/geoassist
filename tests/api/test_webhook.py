import base64
import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from geoassist.api.app import create_app
from geoassist.api.readiness import ReadinessProbe
from geoassist.storage.state import StateStore

SECRET = "secret"
BODY = json.dumps(
    {
        "type": "message",
        "id": "act-1",
        "text": "<at>Bot</at> hola",
        "from": {"id": "u"},
        "conversation": {"id": "c"},
    }
).encode()


def signed(body=BODY):
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).digest()
    return {
        "Authorization": "HMAC " + base64.b64encode(digest).decode(),
        "Content-Type": "application/json",
    }


class FakeService:
    def __init__(self):
        self.calls = 0

    def handle(self, message):
        from geoassist.services.chat import ChatOutcome

        self.calls += 1
        return ChatOutcome("respuesta", False, (), 10)


def app_with(service, state=None):
    from geoassist.teams.outgoing_webhook import OutgoingWebhookAdapter
    from geoassist.api.app import AppDependencies
    from geoassist.config import Settings

    settings = Settings(
        _env_file=None,
        teams_security_token=SECRET,
        llm_api_key="k",
        llm_data_policy="external_approved",
    )
    deps = AppDependencies(
        settings, OutgoingWebhookAdapter(SECRET, "Bot"), service, None, None, state
    )
    return create_app(settings, probe=ReadinessProbe({}), dependencies=deps)


def test_valid_webhook_returns_teams_reply():
    service = FakeService()
    response = TestClient(app_with(service)).post("/teams/webhook", content=BODY, headers=signed())
    assert response.status_code == 200
    assert response.json() == {"type": "message", "text": "respuesta"}


def test_duplicate_activity_uses_cache(tmp_path):
    service = FakeService()
    client = TestClient(app_with(service, StateStore(tmp_path / "state.db")))
    assert client.post("/teams/webhook", content=BODY, headers=signed()).status_code == 200
    assert client.post("/teams/webhook", content=BODY, headers=signed()).status_code == 200
    assert service.calls == 1


def test_invalid_signature_returns_401():
    response = TestClient(app_with(FakeService())).post(
        "/teams/webhook", content=BODY, headers={"Authorization": "HMAC bad"}
    )
    assert response.status_code == 401


def test_legacy_webhook_path_returns_same_reply():
    service = FakeService()
    response = TestClient(app_with(service)).post("/webhook", content=BODY, headers=signed())
    assert response.status_code == 200
    assert response.json() == {"type": "message", "text": "respuesta"}


def _default_app(**settings_overrides):
    from geoassist.api.app import create_app
    from geoassist.config import Settings

    values = dict(
        _env_file=None,
        app_env="development",
        llm_api_key="k",
        llm_data_policy="external_approved",
    )
    values.update(settings_overrides)
    return create_app(Settings(**values), probe=ReadinessProbe({}))


def test_dev_without_token_accepts_unsigned_requests_on_both_paths():
    client = TestClient(_default_app())
    # Sin chat inyectado el handler termina en 503: prueba de que HMAC y parse pasaron.
    assert client.post("/teams/webhook", content=BODY).status_code == 503
    assert client.post("/webhook", content=BODY).status_code == 503


def test_dev_with_token_still_requires_signature():
    client = TestClient(_default_app(teams_security_token=SECRET))
    assert client.post("/teams/webhook", content=BODY).status_code == 401
    assert client.post("/webhook", content=BODY).status_code == 401


def test_oversized_body_returns_413():
    body = b"x" * 70000
    response = TestClient(app_with(FakeService())).post(
        "/teams/webhook", content=body, headers=signed(body)
    )
    assert response.status_code == 413
