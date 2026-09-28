"""Matrix smart threading chooses the session before the gateway claims it."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.event import MessageEvent
from plugins.platforms.matrix.adapter import MatrixAdapter
from plugins.platforms.matrix import thread_choice


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _event(adapter, text="Please make a detailed plan", *, thread=None, reply=None, event_id="$new"):
    source = adapter.build_source(
        chat_id="!lab:example.test", chat_type="group", user_id="@user:example.test",
        thread_id=thread, parent_chat_id="!lab:example.test" if thread else None,
        message_id=event_id,
    )
    return MessageEvent(text=text, source=source, message_id=event_id,
                        reply_to_message_id=reply, channel_context="OLD ROOM HISTORY")


@pytest.mark.anyio
@pytest.mark.parametrize("choice,expected_thread", [(True, "$new"), (False, None)])
async def test_new_group_request_is_routed_before_session_claim(
    monkeypatch, tmp_path, choice, expected_thread,
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = MatrixAdapter(PlatformConfig(extra={"smart_threading": True, "auto_thread": False,
                                                 "session_scope": "room"}))
    adapter._conversation_middleware = SimpleNamespace(
        handlers=[SimpleNamespace(filter_model={"provider": "mistral", "model": "test"})])
    adapter._message_handler = AsyncMock()
    claim = MagicMock(return_value=True)
    adapter._start_session_processing = claim
    classify = AsyncMock(return_value=choice)
    monkeypatch.setattr(thread_choice, "wants_thread", classify)
    event = _event(adapter)

    await adapter._handle_message_after_conversation(event)

    assert event.source.thread_id == expected_thread
    assert event.channel_context == (None if choice else "OLD ROOM HISTORY")
    assert claim.call_args.args[1] == adapter._event_session_key(event)
    assert ("$new" in adapter._threads) is choice
    classify.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("thread,reply,text", [
    ("$existing", None, "Please make a detailed plan"),
    (None, "$prior", "Please make a detailed plan"),
    (None, None, "/stop"),
])
async def test_existing_thread_reply_and_command_keep_their_lane(
    monkeypatch, tmp_path, thread, reply, text,
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = MatrixAdapter(PlatformConfig(extra={"smart_threading": True}))
    adapter._conversation_middleware = SimpleNamespace(handlers=[SimpleNamespace(filter_model={})])
    adapter._message_handler = AsyncMock()
    adapter._start_session_processing = MagicMock(return_value=True)
    classify = AsyncMock(return_value=True)
    monkeypatch.setattr(thread_choice, "wants_thread", classify)
    event = _event(adapter, text, thread=thread, reply=reply)

    await adapter._handle_message_after_conversation(event)

    assert event.source.thread_id == thread
    classify.assert_not_awaited()


@pytest.mark.anyio
async def test_classifier_failure_keeps_main_chat(monkeypatch):
    def unavailable(*args):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(thread_choice, "run_chain", unavailable)
    assert await thread_choice.wants_thread("Please research and implement this", {}) is False


@pytest.mark.anyio
async def test_classifier_receives_only_current_message(monkeypatch):
    captured = {}

    def classify(_model, prompt, _tokens):
        captured["prompt"] = prompt
        return "THREAD", {}

    monkeypatch.setattr(thread_choice, "run_chain", classify)
    assert await thread_choice.wants_thread(
        "Please implement this\n\n[Relevance assessment: private room history]", {})
    assert "private room history" not in captured["prompt"]
    assert "Please implement this" in captured["prompt"]
