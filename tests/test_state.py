from datetime import datetime, timedelta, timezone
from geoassist.storage.state import StateStore, session_key

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


def test_session_is_isolated_by_user(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.append_turn(
        "k1",
        tenant_id="t",
        conversation_id="c",
        user_id="u1",
        question="q1",
        answer="a1",
        sources=[],
        app_hint=None,
        now=NOW,
    )
    store.append_turn(
        "k2",
        tenant_id="t",
        conversation_id="c",
        user_id="u2",
        question="q2",
        answer="a2",
        sources=[],
        app_hint=None,
        now=NOW,
    )
    assert [m.content for m in store.get_session("k1", NOW).history] == ["q1", "a1"]
    assert [m.content for m in store.get_session("k2", NOW).history] == ["q2", "a2"]


def test_history_keeps_last_six_turns(tmp_path):
    store = StateStore(tmp_path / "state.db")
    for i in range(8):
        store.append_turn(
            "k",
            tenant_id=None,
            conversation_id="c",
            user_id="u",
            question=f"q{i}",
            answer=f"a{i}",
            sources=[],
            app_hint=None,
            now=NOW + timedelta(minutes=i),
        )
    history = store.get_session("k", NOW + timedelta(minutes=8)).history
    assert len(history) == 12
    assert history[0].content == "q2"


def test_session_expires(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.append_turn(
        "k",
        tenant_id=None,
        conversation_id="c",
        user_id="u",
        question="q",
        answer="a",
        sources=[],
        app_hint=None,
        now=NOW,
    )
    assert store.get_session("k", NOW + timedelta(minutes=121)).history == ()


def test_activity_cache_expires(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.cache_response("act1", {"type": "message", "text": "hola"}, NOW)
    assert store.get_cached_response("act1", NOW + timedelta(hours=1)) == {
        "type": "message",
        "text": "hola",
    }
    assert store.get_cached_response("act1", NOW + timedelta(hours=25)) is None


def test_cache_response_is_idempotent(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.cache_response("act1", {"type": "message", "text": "primera"}, NOW)
    store.cache_response("act1", {"type": "message", "text": "segunda"}, NOW)
    assert store.get_cached_response("act1", NOW) == {
        "type": "message",
        "text": "segunda",
    }


def test_session_key_uses_default_tenant():
    assert session_key(None, "c", "u") == "default|c|u"
