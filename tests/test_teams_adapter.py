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


def test_verify_is_skipped_when_allow_unsigned_and_empty_secret():
    adapter = OutgoingWebhookAdapter("", "GeoAssist", allow_unsigned=True)
    adapter.verify(BODY, {})
    adapter.verify(BODY, {"Authorization": "HMAC bad"})


def test_constructor_warns_loudly_when_hmac_disabled(capsys):
    from geoassist.logging import configure_logging

    configure_logging("INFO")
    OutgoingWebhookAdapter("", "GeoAssist", allow_unsigned=True)
    output = capsys.readouterr().out
    assert "HMAC DESHABILITADO" in output


def test_constructor_is_silent_when_signature_is_enforced(capsys):
    from geoassist.logging import configure_logging

    configure_logging("INFO")
    OutgoingWebhookAdapter(SECRET, "GeoAssist", allow_unsigned=True)
    OutgoingWebhookAdapter("", "GeoAssist")
    output = capsys.readouterr().out
    assert "DESHABILITADO" not in output


def test_skip_logs_warning_about_unverified_hmac(capsys):
    from geoassist.logging import configure_logging

    configure_logging("INFO")
    OutgoingWebhookAdapter("", "GeoAssist", allow_unsigned=True).verify(BODY, {})
    output = capsys.readouterr().out
    assert "HMAC" in output
    assert "no validado" in output


def test_verify_still_enforced_with_secret_in_unsigned_mode():
    adapter = OutgoingWebhookAdapter(SECRET, "GeoAssist", allow_unsigned=True)
    with pytest.raises(TeamsAuthError):
        adapter.verify(BODY, {})
    adapter.verify(BODY, {"Authorization": signature()})


def test_verify_enforced_without_unsigned_mode_even_with_empty_secret():
    adapter = OutgoingWebhookAdapter("", "GeoAssist")
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


def test_missing_text_raises_invalid_payload():
    body = json.dumps(
        {
            "type": "message",
            "id": "act-1",
            "from": {"id": "user-1"},
            "conversation": {"id": "conv-1"},
        }
    ).encode()
    with pytest.raises(InvalidPayload):
        OutgoingWebhookAdapter(SECRET, "GeoAssist").parse(body)


def test_malformed_from_raises_invalid_payload():
    body = json.dumps(
        {
            "type": "message",
            "id": "act-1",
            "text": "hola",
            "from": "not-a-dict",
            "conversation": {"id": "conv-1"},
        }
    ).encode()
    with pytest.raises(InvalidPayload):
        OutgoingWebhookAdapter(SECRET, "GeoAssist").parse(body)


def test_malformed_conversation_raises_invalid_payload():
    body = json.dumps(
        {
            "type": "message",
            "id": "act-1",
            "text": "hola",
            "from": {"id": "user-1"},
            "conversation": 123,
        }
    ).encode()
    with pytest.raises(InvalidPayload):
        OutgoingWebhookAdapter(SECRET, "GeoAssist").parse(body)


def test_malformed_channel_data_raises_invalid_payload():
    body = json.dumps(
        {
            "type": "message",
            "id": "act-1",
            "text": "hola",
            "from": {"id": "user-1"},
            "conversation": {"id": "conv-1"},
            "channelData": "not-a-dict",
        }
    ).encode()
    with pytest.raises(InvalidPayload):
        OutgoingWebhookAdapter(SECRET, "GeoAssist").parse(body)


def test_malformed_tenant_raises_invalid_payload():
    body = json.dumps(
        {
            "type": "message",
            "id": "act-1",
            "text": "hola",
            "from": {"id": "user-1"},
            "conversation": {"id": "conv-1"},
            "channelData": {"tenant": "not-a-dict"},
        }
    ).encode()
    with pytest.raises(InvalidPayload):
        OutgoingWebhookAdapter(SECRET, "GeoAssist").parse(body)


def test_parse_strips_teams_html_and_entities():
    body = json.dumps(
        {
            "type": "message",
            "id": "act-2",
            "text": "<p><at>Chatbot</at>&nbsp;que hacer cuando el geopricer se queda cargando&nbsp;</p>",
            "from": {"id": "user-1"},
            "conversation": {"id": "conv-1"},
        }
    ).encode()
    message = OutgoingWebhookAdapter(SECRET, "GeoAssist").parse(body)
    assert message.text == "que hacer cuando el geopricer se queda cargando"


def test_parse_strips_any_at_mention_not_only_bot_name():
    body = json.dumps(
        {
            "type": "message",
            "id": "act-3",
            "text": "<at>Otro Nombre</at> hola equipo",
            "from": {"id": "user-1"},
            "conversation": {"id": "conv-1"},
        }
    ).encode()
    message = OutgoingWebhookAdapter(SECRET, "GeoAssist").parse(body)
    assert message.text == "hola equipo"
