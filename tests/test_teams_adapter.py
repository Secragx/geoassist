import base64
import hashlib
import hmac
import json

import pytest
from geoassist.teams.outgoing_webhook import InvalidPayload, OutgoingWebhookAdapter, TeamsAuthError

SECRET = "teams-secret"
BODY = json.dumps(
    {
        "type": "message",
        "id": "act-1",
        "text": "<at>GeoAssist</at> ¿Cómo confirmo una remesa?",
        "from": {"id": "user-1"},
        "conversation": {"id": "conv-1"},
        "channelData": {"tenant": {"id": "tenant-1"}},
    }
).encode()


def signature(body=BODY, secret=SECRET):
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return "HMAC " + base64.b64encode(digest).decode()


def test_valid_signature_is_accepted():
    OutgoingWebhookAdapter(SECRET, "GeoAssist").verify(BODY, {"Authorization": signature()})


def test_invalid_or_missing_signature_is_rejected():
    adapter = OutgoingWebhookAdapter(SECRET, "GeoAssist")
    with pytest.raises(TeamsAuthError):
        adapter.verify(BODY, {"Authorization": "HMAC bad"})
    with pytest.raises(TeamsAuthError):
        adapter.verify(BODY, {})


def test_parse_strips_mention_and_extracts_fields():
    message = OutgoingWebhookAdapter(SECRET, "GeoAssist").parse(BODY)
    assert message.text == "¿Cómo confirmo una remesa?"
    assert (message.activity_id, message.user_id, message.conversation_id) == (
        "act-1",
        "user-1",
        "conv-1",
    )
    assert message.tenant_id == "tenant-1"


def test_non_message_activity_is_ignored():
    body = json.dumps({"type": "conversationUpdate", "id": "x"}).encode()
    assert OutgoingWebhookAdapter(SECRET, "GeoAssist").parse(body) is None


def test_missing_id_raises_invalid_payload():
    body = json.dumps({"type": "message", "text": "hola"}).encode()
    with pytest.raises(InvalidPayload):
        OutgoingWebhookAdapter(SECRET, "GeoAssist").parse(body)


def test_reply_format():
    reply = OutgoingWebhookAdapter(SECRET, "GeoAssist").format_reply("hola")
    assert reply == {"type": "message", "text": "hola"}
