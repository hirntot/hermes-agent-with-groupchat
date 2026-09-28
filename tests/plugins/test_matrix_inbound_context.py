"""Matrix thread roots must reach the prompt even without inline reply fallbacks.

Regression coverage for upstream issues #45493 and #108425.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.matrix.adapter import MatrixAdapter


def adapter(monkeypatch):
    monkeypatch.setenv("MATRIX_AUTO_THREAD", "false")
    monkeypatch.setenv("MATRIX_REQUIRE_MENTION", "false")
    instance = MatrixAdapter(PlatformConfig(enabled=True, extra={
        "homeserver": "https://matrix.example.org", "user_id": "@bot:example.org",
    }))
    instance._resolve_room_identity = AsyncMock(return_value=SimpleNamespace(
        display_name="Room", room_topic=None, server_name="example.org", chat_type="group"))
    instance._is_dm_room = AsyncMock(return_value=False)
    instance._get_display_name = AsyncMock(side_effect=lambda room, user: user)
    return instance


@pytest.mark.asyncio
@pytest.mark.parametrize("thread", [True, False])
async def test_referenced_context_survives_real_client_and_prompt_path(monkeypatch, thread):
    """A real mautrix GET supplies only the root/target from the admitted room."""
    from aiohttp import web, ClientSession
    from mautrix.client import Client
    from gateway.run import GatewayRunner
    from gateway.config import GatewayConfig, Platform

    requests = []
    async def get_event(request):
        room, event = request.match_info["room"], request.match_info["event"]
        requests.append((room, event))
        return web.json_response({
            "type": "m.room.message", "room_id": room, "event_id": event,
            "sender": "@author:example.org", "origin_server_ts": 1,
            "content": {"msgtype": "m.text", "body": f"Topic for {event}"},
        })
    async def relations(request):
        return web.json_response({"chunk": []})
    app = web.Application()
    app.router.add_get("/_matrix/client/v1/rooms/{room}/relations/{event}/m.thread", relations)
    app.router.add_get("/_matrix/client/v3/rooms/{room}/event/{event}", get_event)
    http_runner = web.AppRunner(app)
    await http_runner.setup()
    site = web.TCPSite(http_runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with ClientSession() as session:
            a = adapter(monkeypatch)
            a._client = Client(mxid="@bot:example.org", base_url=f"http://127.0.0.1:{port}",
                               token="test-only", client_session=session)
            runner = object.__new__(GatewayRunner)
            runner.config = GatewayConfig()
            runner.adapters = {Platform.MATRIX: a}
            runner._model = "test"
            runner._base_url = None
            runner._expand_inbound_context_references = AsyncMock(side_effect=lambda source, key, text: text)
            for root in ("$backup", "$glasses", "$backup"):
                relation = {"m.in_reply_to": {"event_id": root}}
                if thread:
                    # The thread root differs from the latest continuation (the real incident).
                    relation.update(rel_type="m.thread", event_id=root, is_falling_back=True)
                    relation["m.in_reply_to"] = {"event_id": "$latest"}
                event = await a._build_inbound_event(
                    "!room:example.org", "@user:example.org", "$current", "Please show text and image",
                    {"msgtype": "m.text"}, relation)
                assert event.text == "Please show text and image"
                if thread:
                    assert event.source.thread_id == root
                    prompt = await runner._prepare_inbound_message_text(
                        event=event, source=event.source, history=[])
                    assert f"Topic for {root}" in prompt
                    assert f"Topic for {'$glasses' if root == '$backup' else '$backup'}" not in prompt
                    assert event.reply_to_text is None  # fallback is not an explicit quote
                else:
                    assert event.reply_to_text == f"Topic for {root}"
                    assert event.reply_to_author_id == "@author:example.org"
            assert requests == [("!room:example.org", r) for r in ("$backup", "$glasses")]
            before = list(requests)
            existing = await runner._prepare_inbound_message_text(
                event=event, source=event.source,
                history=[{"role": "user", "content": "We have moved on to another topic"}])
            assert requests == before
            if thread:
                assert "[Earlier messages in this thread]" not in existing
    finally:
        await http_runner.cleanup()
