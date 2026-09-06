import types
from unittest.mock import AsyncMock, patch

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import SendResult


def _clear_clarify_state():
    from tools import clarify_gateway as cm
    with cm._lock:
        cm._entries.clear()
        cm._session_index.clear()


class TestMatrixClarifyReactions:
    def setup_method(self):
        _clear_clarify_state()

    @pytest.mark.asyncio
    async def test_send_clarify_seeds_semantic_reactions(self, monkeypatch):
        monkeypatch.setenv("MATRIX_ALLOWED_USERS", "@owner:nope.chat")
        from plugins.platforms.matrix.adapter import MatrixAdapter

        adapter = MatrixAdapter(
            PlatformConfig(
                enabled=True,
                token="tok",
                extra={"homeserver": "https://matrix.example.org"},
            )
        )
        adapter._client = object()
        adapter.send = AsyncMock(
            return_value=SendResult(success=True, message_id="$proposal")
        )
        adapter._send_reaction = AsyncMock(
            side_effect=["$seed-ok", "$seed-skip", "$seed-drop"]
        )

        result = await adapter.send_clarify(
            chat_id="!room:nope.chat",
            question="Vollständiger Kommentar",
            choices=[
                "✅ Genau so absenden",
                "❌ Beitrag überspringen",
                "🚮 Person verwerfen",
            ],
            clarify_id="clarify-1",
            session_key="session-1",
            metadata={"requester_user_id": "@owner:nope.chat"},
        )

        assert result.success is True
        assert result.message_id == "$proposal"
        assert adapter._send_reaction.await_args_list == [
            (("!room:nope.chat", "$proposal", "✅"),),
            (("!room:nope.chat", "$proposal", "❌"),),
            (("!room:nope.chat", "$proposal", "🚮"),),
        ]
        prompt = adapter._clarify_prompts_by_event["$proposal"]
        assert prompt.choices == {
            "✅": "✅ Genau so absenden",
            "❌": "❌ Beitrag überspringen",
            "🚮": "🚮 Person verwerfen",
        }
        assert prompt.requester_user_id == "@owner:nope.chat"

    @pytest.mark.asyncio
    async def test_reaction_resolves_clarify_with_full_choice(self, monkeypatch):
        monkeypatch.setenv("MATRIX_ALLOWED_USERS", "@owner:nope.chat")
        from plugins.platforms.matrix.adapter import MatrixAdapter
        from tools import clarify_gateway as cm

        cm.register(
            "clarify-2",
            "session-2",
            "Vollständiger Kommentar",
            ["✅ Genau so absenden", "❌ Beitrag überspringen"],
        )
        adapter = MatrixAdapter(
            PlatformConfig(
                enabled=True,
                token="tok",
                extra={"homeserver": "https://matrix.example.org"},
            )
        )
        adapter._user_id = "@lena:nope.chat"
        adapter._client = object()
        adapter.send = AsyncMock(
            return_value=SendResult(success=True, message_id="$proposal")
        )
        adapter._send_reaction = AsyncMock(
            side_effect=["$seed-ok", "$seed-skip"]
        )
        await adapter.send_clarify(
            chat_id="!room:nope.chat",
            question="Vollständiger Kommentar",
            choices=["✅ Genau so absenden", "❌ Beitrag überspringen"],
            clarify_id="clarify-2",
            session_key="session-2",
            metadata={"requester_user_id": "@owner:nope.chat"},
        )

        event = types.SimpleNamespace(
            sender="@owner:nope.chat",
            event_id="$reaction",
            room_id="!room:nope.chat",
            content={
                "m.relates_to": {
                    "event_id": "$proposal",
                    "key": "✅",
                }
            },
        )
        await adapter._on_reaction(event)

        with cm._lock:
            entry = cm._entries["clarify-2"]
        assert entry.event.is_set()
        assert entry.response == "✅ Genau so absenden"
        assert "$proposal" not in adapter._clarify_prompts_by_event
