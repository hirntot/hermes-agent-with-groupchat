import asyncio
import dataclasses
import xml.etree.ElementTree as ET
from email.message import Message
from urllib.error import HTTPError

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource
from plugins.platforms.matrix.intelligent_reaction import (
    IntelligentReactionGate,
    _load_env_key,
)


def test_score_parser_preserves_zero_for_discard():
    score, rationale = IntelligentReactionGate._parse_score(
        '{"score": 0, "rationale": "Discardable noise."}'
    )
    assert score == 0
    assert rationale == "Discardable noise."


class _AdapterStub:
    def __init__(self):
        self.delivered = []
        self.receipts = []

    async def handle_message(self, event):
        self.delivered.append(event)

    async def send_read_receipt(self, room_id, event_id):
        self.receipts.append((room_id, event_id))


def test_relevance_key_does_not_fall_back_to_global_profile(tmp_path, monkeypatch):
    global_home = tmp_path / "user-home"
    active_profile = tmp_path / "profiles" / "agent_without_key"
    (global_home / ".hermes").mkdir(parents=True)
    active_profile.mkdir(parents=True)
    (global_home / ".hermes" / ".env").write_text(
        "OPENROUTER_API_KEY=global-key-must-not-leak\n"
    )
    (active_profile / ".env").write_text("MATRIX_USER_ID=@agent:example.org\n")
    monkeypatch.setenv("HOME", str(global_home))
    monkeypatch.setenv("HERMES_HOME", str(active_profile))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_KEY", raising=False)

    assert _load_env_key() == ""


@pytest.mark.asyncio
async def test_relevance_uses_codex_last_resort_without_upstream_keys(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    gate = IntelligentReactionGate(_AdapterStub(), config=None)
    gate._mistral_key = ""
    gate._openrouter_key = ""
    gate._codex_model = "gpt-5.6-luna"
    calls = []

    async def _codex(model, prompt, max_tokens=120):
        calls.append(("codex", model))
        return '{"score": 4, "rationale": "Codex hat bewertet."}'

    async def _openrouter(model, prompt, max_tokens=120):
        calls.append(("openrouter", model))
        raise AssertionError("OpenRouter must not run after Codex success")

    monkeypatch.setattr(gate, "_call_codex", _codex, raising=False)
    monkeypatch.setattr(gate, "_call_openrouter", _openrouter)

    assert await gate._evaluate("!group:example.org", "Bitte prüfen", "") == (
        4,
        "Codex hat bewertet.",
    )
    assert calls == [("codex", "gpt-5.6-luna")]


@pytest.mark.asyncio
async def test_relevance_uses_openrouter_before_codex(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    gate = IntelligentReactionGate(_AdapterStub(), config=None)
    gate._mistral_key = ""
    gate._openrouter_key = "configured"
    calls = []

    async def _codex(model, prompt, max_tokens=120):
        calls.append(("codex", model))
        raise AssertionError("Codex must not run after OpenRouter success")

    async def _openrouter(model, prompt, max_tokens=120):
        calls.append(("openrouter", model))
        return '{"score": 3, "rationale": "Mistral-Backup hat bewertet."}'

    monkeypatch.setattr(gate, "_call_codex", _codex, raising=False)
    monkeypatch.setattr(gate, "_call_openrouter", _openrouter)

    assert await gate._evaluate("!group:example.org", "Bitte prüfen", "") == (
        3,
        "Mistral-Backup hat bewertet.",
    )
    assert calls == [("openrouter", gate._model)]


@pytest.mark.asyncio
async def test_relevance_provider_failures_fail_closed_after_codex(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    gate = IntelligentReactionGate(_AdapterStub(), config=None)
    gate._mistral_key = ""
    gate._openrouter_key = "configured"
    gate._codex_model = "gpt-5.6-luna"
    calls = []

    async def _codex_error(model, prompt, max_tokens=120):
        calls.append(("codex", model))
        raise RuntimeError("Codex unavailable")

    async def _payment_error(model, prompt, max_tokens=120):
        calls.append(("openrouter", model))
        raise HTTPError(
            "https://openrouter.ai/api/v1/chat/completions",
            402,
            "Payment Required",
            hdrs=Message(),
            fp=None,
        )

    monkeypatch.setattr(gate, "_call_codex", _codex_error, raising=False)
    monkeypatch.setattr(gate, "_call_openrouter", _payment_error)

    score, rationale = await gate._evaluate(
        "!group:example.org", "Bitte prüfen", ""
    )

    assert score == 1
    assert "classified as information only" in rationale
    assert calls == [
        ("openrouter", gate._model),
        ("openrouter", gate._backup_model),
        ("codex", "gpt-5.6-luna"),
    ]


def _event(chat_id, chat_type, chat_name, text):
    source = SessionSource(
        platform=Platform.MATRIX,
        chat_id=chat_id,
        chat_name=chat_name,
        chat_type=chat_type,
        user_id="@owner:example.org",
        user_name="Owner",
    )
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=source,
        message_id="$event",
        user_id=source.user_id,
        user_name=source.user_name,
    )


def _room_priority(path, room_id):
    root = ET.parse(path).getroot()
    matches = [room for room in root.findall("./rooms/room") if room.get("id") == room_id]
    assert len(matches) == 1
    return matches[0].findtext("answer_priority")


@pytest.mark.asyncio
async def test_unknown_dm_bootstraps_always_context_and_delivers_immediately(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _AdapterStub()
    gate = IntelligentReactionGate(adapter, config=None)
    updates = []
    monkeypatch.setattr(
        gate,
        "_maybe_update_context",
        lambda room="", correction="", force=False: updates.append(
            (room, correction, force)
        ),
    )
    event = _event(
        "!dm:example.org", "dm", "DM mit Owner", "Nur eine kurze DM-Nachricht"
    )

    await gate.process(event, is_mentioned=False)

    assert len(adapter.delivered) == 1
    delivered_text = adapter.delivered[0].text
    assert delivered_text.startswith("Nur eine kurze DM-Nachricht")
    assert "DM mode ALWAYS" in delivered_text
    assert "Kein Raum-Kontext vorhanden" not in delivered_text
    context_file = tmp_path / "RELEVANCE_CONTEXT.xml"
    assert _room_priority(context_file, "!dm:example.org") == "ALWAYS"
    assert updates and updates[0][0] == "!dm:example.org"
    assert "chat_type=dm" in updates[0][1]
    assert "ALWAYS" in updates[0][1]


@pytest.mark.asyncio
async def test_unknown_group_bootstraps_ask_ai_context_for_its_agent(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _AdapterStub()
    gate = IntelligentReactionGate(adapter, config=None)
    updates = []
    monkeypatch.setattr(
        gate,
        "_maybe_update_context",
        lambda room="", correction="", force=False: updates.append(
            (room, correction, force)
        ),
    )

    async def _relevant(*args, **kwargs):
        return 5, "Testnachricht ist relevant."

    monkeypatch.setattr(gate, "_evaluate", _relevant)
    event = _event(
        "!group:example.org", "group", "Projektgruppe", "Bitte einmal prüfen"
    )

    await gate.process(event, is_mentioned=False)

    assert len(adapter.delivered) == 1
    delivered_text = adapter.delivered[0].text
    assert "Kein Raum-Kontext vorhanden" not in delivered_text
    context_file = tmp_path / "RELEVANCE_CONTEXT.xml"
    assert _room_priority(context_file, "!group:example.org") == "ASK_AI"
    assert updates and updates[0][0] == "!group:example.org"
    assert "chat_type=group" in updates[0][1]
    assert "Projektgruppe" in updates[0][1]


@pytest.mark.asyncio
async def test_dm_enrichment_cannot_downgrade_always(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    gate = IntelligentReactionGate(_AdapterStub(), config=None)

    async def _bad_model_choice(*args, **kwargs):
        return (
            '{"answer_priority":"ASK_AI","relevance_factor":10,'
            '"names":["Agent"],"relation":"Modellvorschlag"}'
        )

    monkeypatch.setattr(gate, "_call_model_chain", _bad_model_choice)
    current_xml = """<?xml version="1.0" encoding="UTF-8"?>
<relevance_context><rooms><room id="!dm:example.org" alias="">
<answer_priority>ALWAYS</answer_priority><relevance_factor>99</relevance_factor>
<relation>Direkter DM</relation><names /></room></rooms></relevance_context>"""

    updated = await gate._generate_context(
        "!dm:example.org",
        "Automatisch neu erkannter Matrix-Raum. chat_type=dm; "
        "Verbindliche Regel: answer_priority muss ALWAYS bleiben.",
        current_xml,
    )

    root = ET.fromstring(updated)
    room = root.find("./rooms/room")
    assert room is not None
    assert room.findtext("answer_priority") == "ALWAYS"


def test_default_system_patterns_load_when_env_is_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("MATRIX_INTELLIGENT_REACTION_SYSTEM_PATTERNS", raising=False)

    gate = IntelligentReactionGate(_AdapterStub(), config=None)

    assert gate._is_system_message_one_liner("[gelöscht]")
    assert gate._is_system_message_one_liner(
        "[Relevanz-Einschätzung: nicht relevant]"
    )
    assert gate._is_system_message_one_liner(
        "⚠️ Gateway restarting — Your current task will be interrupted."
    )
    assert gate._is_system_message_one_liner(
        "♻️ Gateway online — Hermes is back and ready."
    )
    assert gate._is_system_message_one_liner(
        "⚡ Interrupting current task (2 min elapsed, iteration 1/500). "
        "I'll respond to your message shortly."
    )
    assert gate._is_system_message_one_liner(
        "⏳ Subagent working — your message is queued for when it finishes."
    )
    assert gate._is_system_message_one_liner(
        "⚠️ Model fallback: primary unavailable; using fallback."
    )


def test_interrupt_notice_is_dropped_only_when_it_is_the_majority(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    gate = IntelligentReactionGate(_AdapterStub(), config=None)
    notice = "⚡ Interrupting current task. I'll respond to your message shortly."

    assert gate._is_interrupt_notice_majority(notice)
    assert gate._is_interrupt_notice_majority(f"Hinweis: {notice}")
    assert gate._is_interrupt_notice_majority(f"{notice}\n{notice}\nkurzer Rest")
    assert not gate._is_interrupt_notice_majority(
        f"{notice} " + ("x" * len(notice))
    )
    assert not gate._is_interrupt_notice_majority(
        f"Bitte bearbeite diese ausführliche echte Anfrage: {notice} "
        + ("wichtiger Inhalt " * 8)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        (
            "[felix_ai] felix_ai: ⚠️ **Dangerous command requires approval**\n"
            "```\nrm /tmp/example\n```"
        ),
        (
            "[bastian_ai] ⚠️ **Confirm /new**\n"
            "This starts a fresh session and discards the current conversation history.\n"
            "Choose: Approve Once, Always Approve, or Cancel."
        ),
        "[lena_ai] That reaction is not valid for this approval prompt.",
    ],
)
async def test_dangerous_approval_lifecycle_is_dropped_before_direct_mention(
    tmp_path, monkeypatch, text
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _AdapterStub()
    gate = IntelligentReactionGate(adapter, config=None)
    event = _event("!group:example.org", "group", "Projektgruppe", text)

    await gate.process(event, is_mentioned=True)

    assert adapter.delivered == []
    assert gate._pending == {}


@pytest.mark.asyncio
async def test_edited_tool_status_is_dropped_before_direct_mention(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _AdapterStub()
    gate = IntelligentReactionGate(adapter, config=None)
    event = _event(
        "!group:example.org",
        "group",
        "Projektgruppe",
        "marlene_ai: [bearbeitet] 📚 Reading skill software-development/systematic-debugging",
    )

    await gate.process(event, is_mentioned=True)

    assert adapter.delivered == []
    assert gate._pending == {}


@pytest.mark.asyncio
async def test_interrupt_notice_majority_preserves_direct_mention(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _AdapterStub()
    gate = IntelligentReactionGate(adapter, config=None)
    notice = "⚡ Interrupting current task. I'll respond to your message shortly."
    event = _event(
        "!group:example.org",
        "group",
        "Projektgruppe",
        f"@agent {notice}",
    )

    await gate.process(event, is_mentioned=True)

    assert len(adapter.delivered) == 1
    assert notice in adapter.delivered[0].text
    assert gate._pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        (
            "⚡ Interrupting current task (2 min elapsed, iteration 1/500). "
            "I'll respond to your message shortly."
        ),
        "⏳ Subagent working — your message is queued for when it finishes.",
        "⚠️ Model fallback: primary unavailable (rate limit); using fallback.",
        (
            "♻️ Recovered reply — the gateway restarted during delivery, "
            "so this may be a duplicate:\n\n"
            "⏳ Gateway is shutting down and is not accepting new work right now."
        ),
    ],
)
async def test_dynamic_hermes_status_never_starts_peer_agent(
    tmp_path, monkeypatch, text
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _AdapterStub()
    gate = IntelligentReactionGate(adapter, config=None)
    event = _event("!group:example.org", "group", "Projektgruppe", text)

    # Reply/mention routing must not turn Hermes lifecycle output into work.
    await gate.process(event, is_mentioned=True)

    assert adapter.delivered == []
    assert gate._pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "   \n\t"])
async def test_empty_text_event_never_starts_agent(tmp_path, monkeypatch, text):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _AdapterStub()
    gate = IntelligentReactionGate(adapter, config=None)
    event = _event("!group:example.org", "group", "Projektgruppe", text)

    await gate.process(event, is_mentioned=True)

    assert adapter.delivered == []
    assert gate._pending == {}


@pytest.mark.asyncio
async def test_matrix_redaction_removes_buffered_original_without_dispatch(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _AdapterStub()
    gate = IntelligentReactionGate(adapter, config=None)
    monkeypatch.setattr(gate, "_maybe_update_context", lambda *args, **kwargs: None)

    async def _irrelevant(*args, **kwargs):
        return 1, "Nicht relevant."

    monkeypatch.setattr(gate, "_throttled_evaluate", _irrelevant)
    room = "!group:example.org"
    original = _event(room, "group", "Projektgruppe", "temporärer Status")

    await gate.process(original, is_mentioned=False)
    assert room in gate._passive_context
    assert gate._passive_context[room][0][0].message_id == "$event"

    redaction = dataclasses.replace(
        _event(
            room,
            "group",
            "Projektgruppe",
            "[DELETE:$event] processing complete",
        ),
        message_id="$redaction",
    )
    await gate.process(redaction, is_mentioned=False)

    assert room not in gate._passive_context
    assert adapter.delivered == []


@pytest.mark.asyncio
async def test_score_one_remains_passive_without_timer(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _AdapterStub()
    gate = IntelligentReactionGate(adapter, config=None)
    monkeypatch.setattr(gate, "_maybe_update_context", lambda *args, **kwargs: None)

    async def _irrelevant(*args, **kwargs):
        return 1, "Kein Bezug zum Agenten."

    monkeypatch.setattr(gate, "_throttled_evaluate", _irrelevant)
    room = "!group:example.org"

    await gate.process(
        _event(room, "group", "Projektgruppe", "Allgemeine Statusmeldung"),
        is_mentioned=False,
    )
    assert adapter.delivered == []
    assert room not in gate._pending
    assert len(gate._passive_context[room]) == 1


@pytest.mark.asyncio
async def test_score_zero_is_discarded_without_context(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _AdapterStub()
    gate = IntelligentReactionGate(adapter, config=None)
    monkeypatch.setattr(gate, "_maybe_update_context", lambda *args, **kwargs: None)

    async def _discard(*args, **kwargs):
        return 0, "Discardable process noise."

    monkeypatch.setattr(gate, "_throttled_evaluate", _discard)
    room = "!group:example.org"
    await gate.process(
        _event(room, "group", "Projektgruppe", "discard me"),
        is_mentioned=False,
    )

    assert adapter.delivered == []
    assert room not in gate._pending
    assert room not in gate._passive_context
    assert room not in gate._room_transcript
