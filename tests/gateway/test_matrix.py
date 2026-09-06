"""Tests for Matrix platform adapter (mautrix-python backend)."""
import asyncio
import re
import stat
import sys
import time
import types
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageType


def _make_fake_mautrix():
    """Create a lightweight set of fake ``mautrix`` modules.

    The adapter does ``from mautrix.api import HTTPAPI``,
    ``from mautrix.client import Client``, ``from mautrix.types import ...``
    at import time and inside methods.  We provide just enough stubs for
    tests that need to mock the mautrix import chain.

    Use via ``patch.dict("sys.modules", _make_fake_mautrix())``.
    """
    # --- mautrix (root) ---
    mautrix = types.ModuleType("mautrix")

    # --- mautrix.api ---
    mautrix_api = types.ModuleType("mautrix.api")

    class HTTPAPI:
        def __init__(self, base_url="", token="", **kwargs):
            self.base_url = base_url
            self.token = token
            self.session = MagicMock()
            self.session.close = AsyncMock()

    mautrix_api.HTTPAPI = HTTPAPI
    mautrix.api = mautrix_api

    # --- mautrix.types ---
    mautrix_types = types.ModuleType("mautrix.types")

    class EventType:
        ROOM_MESSAGE = "m.room.message"
        REACTION = "m.reaction"
        TYPING = "m.typing"
        ROOM_REDACTION = "m.room.redaction"
        ROOM_ENCRYPTED = "m.room.encrypted"
        ROOM_NAME = "m.room.name"

    class UserID(str):
        pass

    class RoomID(str):
        pass

    class EventID(str):
        pass

    class ContentURI(str):
        pass

    class SyncToken(str):
        pass

    class RoomCreatePreset:
        PRIVATE = "private_chat"
        PUBLIC = "public_chat"
        TRUSTED_PRIVATE = "trusted_private_chat"

    class PresenceState:
        ONLINE = "online"
        OFFLINE = "offline"
        UNAVAILABLE = "unavailable"

    class TrustState:
        UNVERIFIED = 0
        VERIFIED = 1

    class PaginationDirection:
        BACKWARD = "b"
        FORWARD = "f"

    mautrix_types.EventType = EventType
    mautrix_types.UserID = UserID
    mautrix_types.RoomID = RoomID
    mautrix_types.EventID = EventID
    mautrix_types.ContentURI = ContentURI
    mautrix_types.SyncToken = SyncToken
    mautrix_types.RoomCreatePreset = RoomCreatePreset
    mautrix_types.PresenceState = PresenceState
    mautrix_types.TrustState = TrustState
    mautrix_types.PaginationDirection = PaginationDirection
    mautrix.types = mautrix_types

    # --- mautrix.client ---
    mautrix_client = types.ModuleType("mautrix.client")

    class Client:
        def __init__(self, mxid=None, device_id=None, api=None,
                     state_store=None, sync_store=None, **kwargs):
            self.mxid = mxid
            self.device_id = device_id
            self.api = api
            self.state_store = state_store
            self.sync_store = sync_store
            self.crypto = None
            self._event_handlers = {}

        def add_event_handler(self, event_type, handler, **kwargs):
            self._event_handlers.setdefault(event_type, []).append(handler)

        def add_dispatcher(self, dispatcher_type):
            pass

    class InternalEventType:
        INVITE = "internal.invite"

    mautrix_client.Client = Client
    mautrix_client.InternalEventType = InternalEventType
    mautrix.client = mautrix_client

    # --- mautrix.client.dispatcher ---
    mautrix_client_dispatcher = types.ModuleType("mautrix.client.dispatcher")

    class MembershipEventDispatcher:
        pass

    mautrix_client_dispatcher.MembershipEventDispatcher = MembershipEventDispatcher

    # --- mautrix.client.state_store ---
    mautrix_client_state_store = types.ModuleType("mautrix.client.state_store")

    class MemoryStateStore:
        async def get_member(self, room_id, user_id):
            return None

        async def get_members(self, room_id):
            return []

        async def get_member_profiles(self, room_id):
            return {}

    class MemorySyncStore:
        def __init__(self):
            self.next_batch = None

        async def get_next_batch(self):
            return self.next_batch

        async def put_next_batch(self, token):
            self.next_batch = token

    mautrix_client_state_store.MemoryStateStore = MemoryStateStore
    mautrix_client_state_store.MemorySyncStore = MemorySyncStore

    # --- mautrix.crypto ---
    mautrix_crypto = types.ModuleType("mautrix.crypto")

    class OlmMachine:
        def __init__(self, client=None, crypto_store=None, state_store=None):
            self.share_keys_min_trust = None
            self.send_keys_min_trust = None

        async def load(self):
            pass

        async def share_keys(self):
            pass

        async def decrypt_megolm_event(self, event):
            return event

    mautrix_crypto.OlmMachine = OlmMachine

    # --- mautrix.crypto.store ---
    mautrix_crypto_store = types.ModuleType("mautrix.crypto.store")

    class MemoryCryptoStore:
        def __init__(self, account_id="", pickle_key=""):  # noqa: S301
            self.account_id = account_id
            self.pickle_key = pickle_key

    mautrix_crypto_store.MemoryCryptoStore = MemoryCryptoStore

    # --- mautrix.crypto.attachments ---
    mautrix_crypto_attachments = types.ModuleType("mautrix.crypto.attachments")

    def encrypt_attachment(data):
        encrypted_file = MagicMock()
        encrypted_file.serialize.return_value = {
            "key": {"k": "testkey"}, "iv": "testiv",
            "hashes": {"sha256": "testhash"}, "v": "v2",
        }
        return (b"ciphertext_" + data, encrypted_file)

    mautrix_crypto_attachments.encrypt_attachment = encrypt_attachment

    # --- mautrix.crypto.store.asyncpg ---
    mautrix_crypto_store_asyncpg = types.ModuleType("mautrix.crypto.store.asyncpg")

    class PgCryptoStore:
        upgrade_table = MagicMock()

        def __init__(self, account_id="", pickle_key="", db=None):  # noqa: S301
            self.account_id = account_id
            self.pickle_key = pickle_key
            self.db = db
            self._device_id = ""

        async def open(self):
            pass

        async def put_device_id(self, device_id):
            self._device_id = device_id

    mautrix_crypto_store_asyncpg.PgCryptoStore = PgCryptoStore

    # --- mautrix.util ---
    mautrix_util = types.ModuleType("mautrix.util")

    # --- mautrix.util.async_db ---
    mautrix_util_async_db = types.ModuleType("mautrix.util.async_db")

    class Database:
        @classmethod
        def create(cls, url, upgrade_table=None):
            db = MagicMock()
            db.start = AsyncMock()
            db.stop = AsyncMock()
            return db

    mautrix_util_async_db.Database = Database

    return {
        "mautrix": mautrix,
        "mautrix.api": mautrix_api,
        "mautrix.types": mautrix_types,
        "mautrix.client": mautrix_client,
        "mautrix.client.dispatcher": mautrix_client_dispatcher,
        "mautrix.client.state_store": mautrix_client_state_store,
        "mautrix.crypto": mautrix_crypto,
        "mautrix.crypto.attachments": mautrix_crypto_attachments,
        "mautrix.crypto.store": mautrix_crypto_store,
        "mautrix.crypto.store.asyncpg": mautrix_crypto_store_asyncpg,
        "mautrix.util": mautrix_util,
        "mautrix.util.async_db": mautrix_util_async_db,
    }


# ---------------------------------------------------------------------------
# Platform & Config
# ---------------------------------------------------------------------------

class TestMatrixConfigLoading:

    def test_apply_env_overrides_with_password(self, monkeypatch):
        monkeypatch.delenv("MATRIX_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("MATRIX_PASSWORD", "secret123")
        monkeypatch.setenv("MATRIX_HOMESERVER", "https://matrix.example.org")
        monkeypatch.setenv("MATRIX_USER_ID", "@bot:example.org")

        from gateway.config import GatewayConfig, _apply_env_overrides
        config = GatewayConfig()
        _apply_env_overrides(config)

        assert Platform.MATRIX in config.platforms
        mc = config.platforms[Platform.MATRIX]
        assert mc.enabled is True
        assert mc.extra.get("password") == "secret123"
        assert mc.extra.get("user_id") == "@bot:example.org"


    def test_matrix_e2ee_mode_optional_sets_config(self, monkeypatch):
        monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "syt_abc123")
        monkeypatch.setenv("MATRIX_HOMESERVER", "https://matrix.example.org")
        monkeypatch.setenv("MATRIX_E2EE_MODE", "optional")
        monkeypatch.delenv("MATRIX_ENCRYPTION", raising=False)

        from gateway.config import GatewayConfig, _apply_env_overrides
        config = GatewayConfig()
        _apply_env_overrides(config)

        mc = config.platforms[Platform.MATRIX]
        assert mc.extra.get("encryption") is True
        assert mc.extra.get("e2ee_mode") == "optional"


    def test_matrix_home_room(self, monkeypatch):
        monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "syt_abc123")
        monkeypatch.setenv("MATRIX_HOMESERVER", "https://matrix.example.org")
        monkeypatch.setenv("MATRIX_HOME_ROOM", "!room123:example.org")
        monkeypatch.setenv("MATRIX_HOME_ROOM_NAME", "Bot Room")

        from gateway.config import GatewayConfig, _apply_env_overrides
        config = GatewayConfig()
        _apply_env_overrides(config)

        home = config.get_home_channel(Platform.MATRIX)
        assert home is not None
        assert home.chat_id == "!room123:example.org"
        assert home.name == "Bot Room"


# ---------------------------------------------------------------------------
# Adapter helpers
# ---------------------------------------------------------------------------

def _make_adapter():
    """Create a MatrixAdapter with mocked config."""
    from plugins.platforms.matrix.adapter import MatrixAdapter
    config = PlatformConfig(
        enabled=True,
        token="syt_test_token",
        extra={
            "homeserver": "https://matrix.example.org",
            "user_id": "@bot:example.org",
        },
    )
    adapter = MatrixAdapter(config)
    return adapter


# ---------------------------------------------------------------------------
# Typing indicator
# ---------------------------------------------------------------------------

class TestMatrixTypingIndicator:
    def setup_method(self):
        self.adapter = _make_adapter()
        self.adapter._client = MagicMock()
        self.adapter._client.set_typing = AsyncMock()

    @pytest.mark.asyncio
    async def test_stop_typing_clears_matrix_typing_state(self):
        """stop_typing() should send typing=false instead of waiting for timeout expiry."""
        from plugins.platforms.matrix.adapter import RoomID

        await self.adapter.stop_typing("!room:example.org")

        self.adapter._client.set_typing.assert_awaited_once_with(
            RoomID("!room:example.org"),
            timeout=0,
        )


# ---------------------------------------------------------------------------
# mxc:// URL conversion
# ---------------------------------------------------------------------------

class TestMatrixMxcToHttp:
    def setup_method(self):
        self.adapter = _make_adapter()


    def test_mxc_with_different_server(self):
        """mxc:// from a different server should still use our homeserver."""
        mxc = "mxc://other.server/media456"
        result = self.adapter._mxc_to_http(mxc)
        assert result.startswith("https://matrix.example.org/")
        assert "other.server/media456" in result


# ---------------------------------------------------------------------------
# DM detection
# ---------------------------------------------------------------------------

class TestMatrixDmDetection:
    def setup_method(self):
        self.adapter = _make_adapter()

    def test_room_in_m_direct_is_dm(self):
        """A room listed in m.direct should be detected as DM."""
        self.adapter._joined_rooms = {"!dm_room:ex.org", "!group_room:ex.org"}
        self.adapter._dm_rooms = {
            "!dm_room:ex.org": True,
            "!group_room:ex.org": False,
        }

        assert self.adapter._dm_rooms.get("!dm_room:ex.org") is True
        assert self.adapter._dm_rooms.get("!group_room:ex.org") is False

    def test_unknown_room_not_in_cache(self):
        """Unknown rooms should not be in the DM cache."""
        self.adapter._dm_rooms = {}
        assert self.adapter._dm_rooms.get("!unknown:ex.org") is None


    @pytest.mark.asyncio
    async def test_named_two_member_dm_is_dm(self):
        """A named two-member room in m.direct is a DM (not a room).

        Most Matrix clients auto-name DM rooms (e.g. "Alice & Bot"), so the
        old `not has_explicit_name` override misclassified them as rooms.
        """
        self.adapter._joined_rooms = {"!named_dm:ex.org"}
        self.adapter._dm_rooms = {"!named_dm:ex.org": True}
        self.adapter._client = MagicMock()
        self.adapter._client.get_state_event = AsyncMock(
            side_effect=lambda room_id, event_type: {"name": "Alice & Bot"}
            if event_type == "m.room.name"
            else (_ for _ in ()).throw(Exception("no alias"))
        )
        self.adapter._client.state_store = MagicMock()
        self.adapter._client.state_store.get_members = AsyncMock(
            return_value=["@bot:ex.org", "@alice:ex.org"]
        )

        identity = await self.adapter._resolve_room_identity("!named_dm:ex.org")

        assert identity.chat_type == "dm"
        assert identity.conflict is False
        assert identity.joined_member_count == 2
        assert await self.adapter._is_dm_room("!named_dm:ex.org") is True


# ---------------------------------------------------------------------------
# Reply fallback stripping
# ---------------------------------------------------------------------------

class TestMatrixReplyFallbackStripping:
    """Test that Matrix reply fallback lines ('> ' prefix) are stripped."""

    def setup_method(self):
        self.adapter = _make_adapter()
        self.adapter._user_id = "@bot:example.org"
        self.adapter._startup_ts = 0.0
        self.adapter._dm_rooms = {}
        self.adapter._message_handler = AsyncMock()

    def _strip_fallback(self, body: str, has_reply: bool = True) -> str:
        """Simulate the reply fallback stripping logic from _on_room_message."""
        reply_to = "some_event_id" if has_reply else None
        if reply_to and body.startswith("> "):
            lines = body.split("\n")
            stripped = []
            past_fallback = False
            for line in lines:
                if not past_fallback:
                    if line.startswith("> ") or line == ">":
                        continue
                    if line == "":
                        past_fallback = True
                        continue
                    past_fallback = True
                stripped.append(line)
            body = "\n".join(stripped) if stripped else body
        return body

    def test_simple_reply_fallback(self):
        body = "> <@alice:ex.org> Original message\n\nActual reply"
        result = self._strip_fallback(body)
        assert result == "Actual reply"

    def test_multiline_reply_fallback(self):
        body = "> <@alice:ex.org> Line 1\n> Line 2\n\nMy response"
        result = self._strip_fallback(body)
        assert result == "My response"


# ---------------------------------------------------------------------------
# Matrix-friendly command aliases
# ---------------------------------------------------------------------------

class TestMatrixBangCommandAlias:
    """Matrix clients may reserve /commands, so Hermes supports !commands."""

    def setup_method(self):
        self.adapter = _make_adapter()
        self.adapter._is_dm_room = AsyncMock(return_value=True)
        self.adapter._get_display_name = AsyncMock(return_value="Alice")
        self.adapter._background_read_receipt = MagicMock()
        self.adapter._text_batch_delay_seconds = 0

    async def _dispatch_text(self, body: str, *, is_dm: bool = True):
        captured_event = None
        self.adapter._is_dm_room = AsyncMock(return_value=is_dm)
        self.adapter._require_mention = True
        self.adapter._free_rooms = set()

        async def capture(msg_event):
            nonlocal captured_event
            captured_event = msg_event

        self.adapter.handle_message = capture
        await self.adapter._handle_text_message(
            room_id="!room:example.org",
            sender="@alice:example.org",
            event_id="$matrix-command-test",
            event_ts=0.0,
            source_content={"msgtype": "m.text", "body": body},
            relates_to={},
        )
        return captured_event

    async def _dispatch_text_reply(self, body: str, *, is_dm: bool = True):
        """Dispatch a message that is a Matrix reply (m.in_reply_to set), so
        the reply-fallback quote stripping path runs before command detection.
        """
        captured_event = None
        self.adapter._is_dm_room = AsyncMock(return_value=is_dm)
        self.adapter._require_mention = True
        self.adapter._free_rooms = set()

        async def capture(msg_event):
            nonlocal captured_event
            captured_event = msg_event

        self.adapter.handle_message = capture
        await self.adapter._handle_text_message(
            room_id="!room:example.org",
            sender="@alice:example.org",
            event_id="$matrix-reply-command-test",
            event_ts=0.0,
            source_content={"msgtype": "m.text", "body": body},
            relates_to={"m.in_reply_to": {"event_id": "$parent-event"}},
        )
        return captured_event

    def test_known_bang_command_normalizes_to_slash_command(self):
        from plugins.platforms.matrix.adapter import _normalize_matrix_bang_command

        assert _normalize_matrix_bang_command("!model") == "/model"
        assert (
            _normalize_matrix_bang_command("!queue continue the plan")
            == "/queue continue the plan"
        )
        assert (
            _normalize_matrix_bang_command("!btw research this")
            == "/btw research this"
        )
        assert _normalize_matrix_bang_command("!tasks") == "/tasks"


    @pytest.mark.asyncio
    async def test_unknown_bang_text_stays_normal_text(self):
        captured_event = await self._dispatch_text("!important note")

        assert captured_event is not None
        assert captured_event.text == "!important note"
        assert captured_event.message_type == MessageType.TEXT
        assert captured_event.get_command() is None


    def test_bang_skill_command_normalizes(self):
        """The get_skill_commands() branch normalizes installed skill
        commands, not just built-in gateway commands. Skill keys are stored
        slash-prefixed (e.g. "/arxiv"), which the resolver must account for."""
        import agent.skill_commands as skill_commands_mod

        fake_skills = {"/arxiv": {}, "/obsidian": {}}
        with patch.object(
            skill_commands_mod, "get_skill_commands", return_value=fake_skills
        ):
            from plugins.platforms.matrix.adapter import _normalize_matrix_bang_command

            # is_gateway_known_command won't know these; the skill branch must.
            assert _normalize_matrix_bang_command("!arxiv") == "/arxiv"
            assert (
                _normalize_matrix_bang_command("!obsidian search foo")
                == "/obsidian search foo"
            )
            # A name in neither registry stays plain text.
            assert (
                _normalize_matrix_bang_command("!definitelynotacommand")
                == "!definitelynotacommand"
            )


    @pytest.mark.asyncio
    async def test_slash_command_in_quoted_reply_normalizes(self):
        """Sanity: the slash equivalent already works post-strip — the bang
        form above must reach parity with this."""
        captured_event = await self._dispatch_text_reply(
            "> <@bob:example.org> earlier message\n\n/model"
        )

        assert captured_event is not None
        assert captured_event.text == "/model"
        assert captured_event.message_type == MessageType.COMMAND


# ---------------------------------------------------------------------------
# Thread detection
# ---------------------------------------------------------------------------

class TestMatrixThreadDetection:


    def test_no_thread_for_edit(self):
        """m.replace relation should not set thread_id."""
        relates_to = {
            "rel_type": "m.replace",
            "event_id": "$edited_event",
        }
        thread_id = None
        if relates_to.get("rel_type") == "m.thread":
            thread_id = relates_to.get("event_id")
        assert thread_id is None


# ---------------------------------------------------------------------------
# Format message
# ---------------------------------------------------------------------------

class TestMatrixFormatMessage:
    def setup_method(self):
        self.adapter = _make_adapter()

    def test_image_markdown_stripped(self):
        """![alt](url) should be converted to just the URL."""
        result = self.adapter.format_message("![cat](https://img.example.com/cat.png)")
        assert result == "https://img.example.com/cat.png"


# ---------------------------------------------------------------------------
# Rendering payloads
# ---------------------------------------------------------------------------

class TestMatrixRenderingPayloads:
    @pytest.fixture(autouse=True)
    def _allow_pingpong_guard(self, monkeypatch):
        process = MagicMock()
        process.returncode = 0
        process.communicate = AsyncMock(return_value=(b"ALLOW", b""))
        monkeypatch.setattr(
            asyncio,
            "create_subprocess_exec",
            AsyncMock(return_value=process),
        )

    def setup_method(self):
        self.adapter = _make_adapter()
        self.mock_client = MagicMock()
        self.mock_client.send_message_event = AsyncMock(return_value="$evt")
        self.adapter._client = self.mock_client

    def _sent_contents(self):
        return [
            call.args[2] if len(call.args) > 2 else call.kwargs["content"]
            for call in self.mock_client.send_message_event.await_args_list
        ]


    @pytest.mark.asyncio
    async def test_thread_payload_uses_m_thread_with_reply_fallback(self):
        result = await self.adapter.send(
            "!room:example.org",
            "threaded",
            metadata={"thread_id": "$root"},
        )

        assert result.success is True
        relates_to = self._sent_contents()[0]["m.relates_to"]
        assert relates_to == {
            "rel_type": "m.thread",
            "event_id": "$root",
            "is_falling_back": True,
            "m.in_reply_to": {"event_id": "$root"},
        }


    @pytest.mark.asyncio
    async def test_long_response_split_preserves_thread_context(self):
        # Build a payload guaranteed to exceed the adapter's outbound chunk
        # size (configurable since #53026) so send() must split it.
        repeats = (self.adapter.max_message_length // 15) + 200
        long_text = "Intro\n```python\n" + ("print('hello')\n" * repeats) + "```\nDone"

        result = await self.adapter.send(
            "!room:example.org",
            long_text,
            metadata={"thread_id": "$root"},
        )

        assert result.success is True
        contents = self._sent_contents()
        assert len(contents) > 1
        for content in contents:
            assert content["m.relates_to"]["rel_type"] == "m.thread"
            assert content["m.relates_to"]["event_id"] == "$root"
            assert content["m.relates_to"]["m.in_reply_to"] == {"event_id": "$root"}
            assert content["body"].count("```") % 2 == 0


# ---------------------------------------------------------------------------
# Markdown to HTML conversion
# ---------------------------------------------------------------------------

class TestMatrixMarkdownToHtml:
    def setup_method(self):
        self.adapter = _make_adapter()

    def test_bold_conversion(self):
        """**bold** should produce <strong> tags."""
        result = self.adapter._markdown_to_html("**bold**")
        assert "<strong>" in result or "<b>" in result
        assert "bold" in result

    def test_italic_conversion(self):
        """*italic* should produce <em> tags."""
        result = self.adapter._markdown_to_html("*italic*")
        assert "<em>" in result or "<i>" in result

    def test_inline_code(self):
        """`code` should produce <code> tags."""
        result = self.adapter._markdown_to_html("`code`")
        assert "<code>" in result

    def test_plain_text_returns_html(self):
        """Plain text should still be returned (possibly with <br> or <p>)."""
        result = self.adapter._markdown_to_html("Hello world")
        assert "Hello world" in result


    def test_matrix_markdown_preserves_table_structure(self):
        table = "\n".join(
            [
                "| Item | Quantity |",
                "| --- | --- |",
                "| Apples | 4 |",
                "| Bread | 1 |",
            ]
        )

        result = self.adapter._markdown_to_html(table)

        assert "<table>" in result
        assert "<thead>" in result
        assert "<tbody>" in result
        assert "<th>Item</th>" in result
        assert "<td>Apples</td>" in result


# ---------------------------------------------------------------------------
# Helper: display name extraction
# ---------------------------------------------------------------------------

class TestMatrixDisplayName:
    def setup_method(self):
        self.adapter = _make_adapter()

    @pytest.mark.asyncio
    async def test_get_display_name_from_state_store(self):
        """Should get display name from state_store.get_member()."""
        mock_member = MagicMock()
        mock_member.displayname = "Alice"

        mock_state_store = MagicMock()
        mock_state_store.get_member = AsyncMock(return_value=mock_member)

        mock_client = MagicMock()
        mock_client.state_store = mock_state_store
        self.adapter._client = mock_client

        name = await self.adapter._get_display_name("!room:ex.org", "@alice:ex.org")
        assert name == "Alice"


# ---------------------------------------------------------------------------
# Requirements check
# ---------------------------------------------------------------------------

class TestMatrixModuleImport:
    def test_module_importable_without_mautrix(self):
        """plugins.platforms.matrix.adapter must be importable even when mautrix is
        not installed — otherwise the gateway crashes for ALL platforms.

        This test uses a subprocess to avoid polluting the current process's
        sys.modules (reimporting a module creates a second module object whose
        classes don't share globals with the original — breaking patch.object
        in subsequent tests).
        """
        import subprocess
        result = subprocess.run(
            [sys.executable, "-c", (
                "import sys\n"
                "# Block mautrix completely\n"
                "class _Blocker:\n"
                "    def find_module(self, name, path=None):\n"
                "        if name.startswith('mautrix'): return self\n"
                "    def load_module(self, name):\n"
                "        raise ImportError(f'blocked: {name}')\n"
                "sys.meta_path.insert(0, _Blocker())\n"
                "for k in list(sys.modules):\n"
                "    if k.startswith('mautrix'): del sys.modules[k]\n"
                "from unittest.mock import patch\n"
                "from plugins.platforms.matrix.adapter import check_matrix_requirements\n"
                "with patch('tools.lazy_deps.ensure', side_effect=ImportError('blocked')):\n"
                "    assert not check_matrix_requirements()\n"
                "print('OK')\n"
            )],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, (
            f"Subprocess failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


class TestMatrixRequirements:


    def test_check_requirements_encryption_true_no_e2ee_deps(self, monkeypatch):
        """MATRIX_ENCRYPTION=true should fail if python-olm is not installed."""
        monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "syt_test")
        monkeypatch.setenv("MATRIX_HOMESERVER", "https://matrix.example.org")
        monkeypatch.setenv("MATRIX_ENCRYPTION", "true")

        import plugins.platforms.matrix.adapter as matrix_mod
        with patch.object(matrix_mod, "_check_e2ee_deps", return_value=False), \
             patch("tools.lazy_deps.feature_missing", return_value=()):
            assert matrix_mod.check_matrix_requirements() is False

    def test_check_requirements_e2ee_optional_no_deps_ok(self, monkeypatch):
        """MATRIX_E2EE_MODE=optional should not block startup without python-olm."""
        monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "syt_test")
        monkeypatch.setenv("MATRIX_HOMESERVER", "https://matrix.example.org")
        monkeypatch.setenv("MATRIX_E2EE_MODE", "optional")
        monkeypatch.delenv("MATRIX_ENCRYPTION", raising=False)

        import plugins.platforms.matrix.adapter as matrix_mod
        with patch.object(matrix_mod, "_check_e2ee_deps", return_value=False), \
             patch("tools.lazy_deps.feature_missing", return_value=()), \
             patch("tools.lazy_deps.ensure_and_bind", return_value=True):
            assert matrix_mod.check_matrix_requirements() is True

    def test_check_requirements_encryption_false_no_e2ee_deps_ok(self, monkeypatch):
        """Without encryption, missing E2EE deps should not block startup."""
        monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "syt_test")
        monkeypatch.setenv("MATRIX_HOMESERVER", "https://matrix.example.org")
        monkeypatch.delenv("MATRIX_ENCRYPTION", raising=False)

        import plugins.platforms.matrix.adapter as matrix_mod
        with patch.object(matrix_mod, "_check_e2ee_deps", return_value=False), \
             patch("tools.lazy_deps.feature_missing", return_value=()):
            assert matrix_mod.check_matrix_requirements() is True

    def test_check_requirements_encryption_true_with_e2ee_deps(self, monkeypatch):
        """MATRIX_ENCRYPTION=true should pass if E2EE deps are available."""
        monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "syt_test")
        monkeypatch.setenv("MATRIX_HOMESERVER", "https://matrix.example.org")
        monkeypatch.setenv("MATRIX_ENCRYPTION", "true")

        import plugins.platforms.matrix.adapter as matrix_mod
        with patch.object(matrix_mod, "_check_e2ee_deps", return_value=True), \
             patch("tools.lazy_deps.feature_missing", return_value=()):
            assert matrix_mod.check_matrix_requirements() is True

    def test_check_e2ee_deps_requires_asyncpg(self, monkeypatch):
        """E2EE deps check must reject when asyncpg is missing — even if olm is present.

        Regression for #31116: ``mautrix[encryption]`` extra installs python-olm
        but NOT asyncpg/aiosqlite, which are required by mautrix's crypto store
        at connect time.  ``_check_e2ee_deps`` previously only tested
        ``OlmMachine`` import and returned True, so the failure manifested as
        a confusing ``No module named 'asyncpg'`` deep in
        ``MatrixAdapter.connect()``.
        """
        from plugins.platforms.matrix.adapter import _check_e2ee_deps
        import builtins
        real_import = builtins.__import__

        def _blocking_import(name, *args, **kwargs):
            if name == "asyncpg" or name.startswith("asyncpg."):
                raise ImportError("blocked for test")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", _blocking_import):
            assert _check_e2ee_deps() is False

    def test_check_e2ee_deps_requires_aiosqlite(self):
        """E2EE deps check must reject when aiosqlite is missing.

        Mautrix's ``Database.create("sqlite:///...")`` driver lookup imports
        aiosqlite lazily — without it, connect fails at ``crypto_db.start()``.
        """
        from plugins.platforms.matrix.adapter import _check_e2ee_deps
        import builtins
        real_import = builtins.__import__

        def _blocking_import(name, *args, **kwargs):
            if name == "aiosqlite" or name.startswith("aiosqlite."):
                raise ImportError("blocked for test")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", _blocking_import):
            assert _check_e2ee_deps() is False

    def test_check_requirements_runs_lazy_install_when_partial(self, monkeypatch):
        """When mautrix is installed but asyncpg/aiosqlite are missing,
        check_matrix_requirements must still run the lazy installer.

        Regression for #31116: the previous ``try: import mautrix`` gate
        short-circuited the install of the OTHER 4 platform.matrix packages,
        so a partial install (mautrix only) was treated as fully installed.
        """
        monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "syt_test")
        monkeypatch.setenv("MATRIX_HOMESERVER", "https://matrix.example.org")
        monkeypatch.delenv("MATRIX_ENCRYPTION", raising=False)

        import plugins.platforms.matrix.adapter as matrix_mod

        # Simulate "mautrix installed, asyncpg missing" → feature_missing
        # returns a non-empty tuple → ensure_and_bind MUST be called.
        called = {"ensure_and_bind": False}

        def _fake_ensure_and_bind(feature, importer, target_globals, **kwargs):
            called["ensure_and_bind"] = True
            assert feature == "platform.matrix"
            return True  # Pretend install succeeded.

        with patch("tools.lazy_deps.feature_missing", return_value=("asyncpg==0.31.0",)), \
             patch("tools.lazy_deps.ensure_and_bind", side_effect=_fake_ensure_and_bind):
            matrix_mod.check_matrix_requirements()

        assert called["ensure_and_bind"], (
            "check_matrix_requirements must call ensure_and_bind whenever ANY "
            "platform.matrix dep is missing, not just when mautrix itself is "
            "missing (#31116)"
        )


# ---------------------------------------------------------------------------
# Access-token auth / E2EE bootstrap
# ---------------------------------------------------------------------------

class TestMatrixAccessTokenAuth:
    @pytest.mark.asyncio
    async def test_connect_with_access_token_and_encryption(self):
        """connect() should call whoami, set user_id/device_id, set up crypto."""
        from plugins.platforms.matrix.adapter import MatrixAdapter

        config = PlatformConfig(
            enabled=True,
            token="syt_test_access_token",
            extra={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
                "encryption": True,
            },
        )
        adapter = MatrixAdapter(config)

        class FakeWhoamiResponse:
            def __init__(self, user_id, device_id):
                self.user_id = user_id
                self.device_id = device_id

        fake_mautrix_mods = _make_fake_mautrix()

        # Create a mock client that returns from the mautrix.client.Client constructor
        mock_client = MagicMock()
        mock_client.mxid = "@bot:example.org"
        mock_client.device_id = None
        mock_client.state_store = MagicMock()
        mock_client.sync_store = MagicMock()
        mock_client.crypto = None
        mock_client.whoami = AsyncMock(return_value=FakeWhoamiResponse("@bot:example.org", "DEV123"))
        mock_client.sync = AsyncMock(return_value={"rooms": {"join": {"!room:server": {}}}})
        mock_client.add_event_handler = MagicMock()
        mock_client.handle_sync = MagicMock(return_value=[])
        mock_client.query_keys = AsyncMock(return_value={
            "device_keys": {"@bot:example.org": {"DEV123": {
                "keys": {"ed25519:DEV123": "fake_ed25519_key"},
            }}},
        })
        mock_client.api = MagicMock()
        mock_client.api.token = "syt_test_access_token"
        mock_client.api.session = MagicMock()
        mock_client.api.session.close = AsyncMock()

        # Mock the crypto setup
        mock_olm = MagicMock()
        mock_olm.load = AsyncMock()
        mock_olm.share_keys = AsyncMock()
        mock_olm.share_keys_min_trust = None
        mock_olm.send_keys_min_trust = None
        mock_olm.account = MagicMock()
        mock_olm.account.identity_keys = {"ed25519": "fake_ed25519_key"}

        # Patch Client constructor to return our mock
        fake_mautrix_mods["mautrix.client"].Client = MagicMock(return_value=mock_client)
        fake_mautrix_mods["mautrix.crypto"].OlmMachine = MagicMock(return_value=mock_olm)

        import plugins.platforms.matrix.adapter as matrix_mod
        with patch.object(matrix_mod, "_check_e2ee_deps", return_value=True):
            with patch.dict("sys.modules", fake_mautrix_mods):
                with patch.object(adapter, "_refresh_dm_cache", AsyncMock()):
                    with patch.object(adapter, "_sync_loop", AsyncMock(return_value=None)):
                        assert await adapter.connect() is True

        mock_client.whoami.assert_awaited_once()
        assert adapter._user_id == "@bot:example.org"

        await adapter.disconnect()


class TestDeviceKeyReVerification:
    @pytest.mark.asyncio
    async def test_verify_fails_when_server_keys_mismatch_after_upload(self):
        """share_keys() succeeds but server still has old keys -> should return False."""
        adapter = _make_adapter()

        mock_client = MagicMock()
        mock_client.mxid = "@bot:example.org"
        mock_client.device_id = "TESTDEVICE"

        # First query: keys missing -> triggers share_keys
        # Second query: keys still don't match -> should fail
        mock_keys_missing = MagicMock()
        mock_keys_missing.device_keys = {"@bot:example.org": {}}

        mock_keys_mismatch = MagicMock()
        mock_device = MagicMock()
        mock_device.keys = {"ed25519:TESTDEVICE": "server_old_key"}
        mock_keys_mismatch.device_keys = {"@bot:example.org": {"TESTDEVICE": mock_device}}

        mock_client.query_keys = AsyncMock(side_effect=[mock_keys_missing, mock_keys_mismatch])

        mock_olm = MagicMock()
        mock_olm.account = MagicMock()
        mock_olm.account.shared = False
        mock_olm.account.identity_keys = {"ed25519": "local_new_key"}
        mock_olm.share_keys = AsyncMock()

        result = await adapter._verify_device_keys_on_server(mock_client, mock_olm)

        assert result is False
        mock_olm.share_keys.assert_awaited_once()


class TestMatrixE2EEHardFail:
    """connect() must refuse to start when E2EE is requested but deps are missing."""

    @pytest.mark.asyncio
    async def test_connect_fails_when_encryption_true_but_no_e2ee_deps(self):
        from plugins.platforms.matrix.adapter import MatrixAdapter

        config = PlatformConfig(
            enabled=True,
            token="syt_test_access_token",
            extra={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
                "encryption": True,
            },
        )
        adapter = MatrixAdapter(config)

        fake_mautrix_mods = _make_fake_mautrix()

        mock_client = MagicMock()
        mock_client.whoami = AsyncMock(return_value=MagicMock(user_id="@bot:example.org", device_id="DEV123"))
        mock_client.api = MagicMock()
        mock_client.api.token = "syt_test_access_token"
        mock_client.api.session = MagicMock()
        mock_client.api.session.close = AsyncMock()
        mock_client.mxid = "@bot:example.org"
        mock_client.device_id = None
        mock_client.crypto = None

        fake_mautrix_mods["mautrix.client"].Client = MagicMock(return_value=mock_client)

        import plugins.platforms.matrix.adapter as matrix_mod
        with patch.object(matrix_mod, "_check_e2ee_deps", return_value=False):
            with patch.dict("sys.modules", fake_mautrix_mods):
                with patch.object(adapter, "_sync_loop", AsyncMock(return_value=None)):
                    result = await adapter.connect()

        assert result is False

    @pytest.mark.asyncio
    async def test_connect_continues_when_e2ee_optional_but_no_deps(self):
        from plugins.platforms.matrix.adapter import MatrixAdapter

        config = PlatformConfig(
            enabled=True,
            token="syt_test_access_token",
            extra={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
                "e2ee_mode": "optional",
            },
        )
        adapter = MatrixAdapter(config)

        fake_mautrix_mods = _make_fake_mautrix()

        mock_sync_store = MagicMock()
        mock_sync_store.get_next_batch = AsyncMock(return_value=None)
        mock_sync_store.put_next_batch = AsyncMock()

        mock_client = MagicMock()
        mock_client.whoami = AsyncMock(return_value=MagicMock(user_id="@bot:example.org", device_id="DEV123"))
        mock_client.api = MagicMock()
        mock_client.api.token = "syt_test_access_token"
        mock_client.api.session = MagicMock()
        mock_client.api.session.close = AsyncMock()
        mock_client.mxid = "@bot:example.org"
        mock_client.device_id = None
        mock_client.crypto = None
        mock_client.sync_store = mock_sync_store
        mock_client.sync = AsyncMock(return_value={"rooms": {"join": {}}, "next_batch": "s1"})
        mock_client.get_account_data = AsyncMock(return_value=MagicMock(content={}))
        mock_client.add_dispatcher = MagicMock()
        mock_client.add_event_handler = MagicMock()
        mock_client.handle_sync = MagicMock(return_value=[])

        fake_mautrix_mods["mautrix.client"].Client = MagicMock(return_value=mock_client)

        import plugins.platforms.matrix.adapter as matrix_mod
        with patch.object(matrix_mod, "_check_e2ee_deps", return_value=False):
            with patch.dict("sys.modules", fake_mautrix_mods):
                with patch.object(matrix_mod, "_create_matrix_session", return_value=MagicMock()):
                    with patch.object(adapter, "_sync_loop", AsyncMock(return_value=None)):
                        result = await adapter.connect()

        assert result is True
        assert adapter._encryption is False
        await adapter.disconnect()


class TestMatrixDeviceId:
    """MATRIX_DEVICE_ID should be used for stable device identity."""


    def test_device_id_config_takes_precedence_over_env(self, monkeypatch):
        monkeypatch.setenv("MATRIX_DEVICE_ID", "FROM_ENV")

        from plugins.platforms.matrix.adapter import MatrixAdapter

        config = PlatformConfig(
            enabled=True,
            token="syt_test",
            extra={
                "homeserver": "https://matrix.example.org",
                "device_id": "FROM_CONFIG",
            },
        )
        adapter = MatrixAdapter(config)
        assert adapter._device_id == "FROM_CONFIG"

    @pytest.mark.asyncio
    async def test_connect_keeps_configured_device_id_on_adapter(self):
        """MATRIX_DEVICE_ID stays on the adapter regardless of whoami.

        Note: this test previously asserted that the configured device_id
        overrides the whoami device_id outright. That is no longer true for
        the *client* identity — a token can only upload keys for its own
        device, so a conflicting whoami device now wins (see
        TestCryptoStoreResetOnDeviceChange). The configured value is still
        preferred when whoami reports no device, and is still recorded on the
        adapter, which is what this test pins.
        """
        from plugins.platforms.matrix.adapter import MatrixAdapter

        config = PlatformConfig(
            enabled=True,
            token="syt_test_access_token",
            extra={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
                "encryption": True,
                "device_id": "MY_STABLE_DEVICE",
            },
        )
        adapter = MatrixAdapter(config)

        fake_mautrix_mods = _make_fake_mautrix()

        mock_client = MagicMock()
        mock_client.mxid = "@bot:example.org"
        mock_client.device_id = None
        mock_client.state_store = MagicMock()
        mock_client.sync_store = MagicMock()
        mock_client.crypto = None
        mock_client.whoami = AsyncMock(return_value=MagicMock(user_id="@bot:example.org", device_id="WHOAMI_DEV"))
        mock_client.sync = AsyncMock(return_value={"rooms": {"join": {"!room:server": {}}}})
        mock_client.add_event_handler = MagicMock()
        mock_client.handle_sync = MagicMock(return_value=[])
        mock_client.query_keys = AsyncMock(return_value={
            "device_keys": {"@bot:example.org": {"MY_STABLE_DEVICE": {
                "keys": {"ed25519:MY_STABLE_DEVICE": "fake_ed25519_key"},
            }}},
        })
        mock_client.api = MagicMock()
        mock_client.api.token = "syt_test_access_token"
        mock_client.api.session = MagicMock()
        mock_client.api.session.close = AsyncMock()

        mock_olm = MagicMock()
        mock_olm.load = AsyncMock()
        mock_olm.share_keys = AsyncMock()
        mock_olm.share_keys_min_trust = None
        mock_olm.send_keys_min_trust = None
        mock_olm.account = MagicMock()
        mock_olm.account.identity_keys = {"ed25519": "fake_ed25519_key"}

        fake_mautrix_mods["mautrix.client"].Client = MagicMock(return_value=mock_client)
        fake_mautrix_mods["mautrix.crypto"].OlmMachine = MagicMock(return_value=mock_olm)

        import plugins.platforms.matrix.adapter as matrix_mod
        with patch.object(matrix_mod, "_check_e2ee_deps", return_value=True):
            with patch.dict("sys.modules", fake_mautrix_mods):
                with patch.object(adapter, "_refresh_dm_cache", AsyncMock()):
                    with patch.object(adapter, "_sync_loop", AsyncMock(return_value=None)):
                        assert await adapter.connect() is True

        # The configured device_id is retained on the adapter.
        assert adapter._device_id == "MY_STABLE_DEVICE"
        # But the token's own device is what the client claims, because the
        # homeserver will not accept key uploads for any other device.
        assert mock_client.device_id == "WHOAMI_DEV"

        await adapter.disconnect()


class TestMatrixPasswordLoginDeviceId:
    """MATRIX_DEVICE_ID should be passed to mautrix Client even with password login."""

    @pytest.mark.asyncio
    async def test_password_login_uses_device_id(self):
        from plugins.platforms.matrix.adapter import MatrixAdapter

        config = PlatformConfig(
            enabled=True,
            extra={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
                "password": "secret",
                "device_id": "STABLE_PW_DEVICE",
            },
        )
        adapter = MatrixAdapter(config)

        fake_mautrix_mods = _make_fake_mautrix()

        mock_client = MagicMock()
        mock_client.mxid = "@bot:example.org"
        mock_client.device_id = None
        mock_client.state_store = MagicMock()
        mock_client.sync_store = MagicMock()
        mock_client.crypto = None
        mock_client.login = AsyncMock(return_value=MagicMock(device_id="STABLE_PW_DEVICE", access_token="tok"))
        mock_client.sync = AsyncMock(return_value={"rooms": {"join": {}}})
        mock_client.add_event_handler = MagicMock()
        mock_client.api = MagicMock()
        mock_client.api.token = ""
        mock_client.api.session = MagicMock()
        mock_client.api.session.close = AsyncMock()

        fake_mautrix_mods["mautrix.client"].Client = MagicMock(return_value=mock_client)

        with patch.dict("sys.modules", fake_mautrix_mods):
            with patch.object(adapter, "_refresh_dm_cache", AsyncMock()):
                with patch.object(adapter, "_sync_loop", AsyncMock(return_value=None)):
                    assert await adapter.connect() is True

        mock_client.login.assert_awaited_once()
        assert adapter._device_id == "STABLE_PW_DEVICE"

        await adapter.disconnect()


class TestMatrixDeviceIdConfig:
    """MATRIX_DEVICE_ID should be plumbed through gateway config."""

    def test_device_id_in_config_extra(self, monkeypatch):
        monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "syt_abc123")
        monkeypatch.setenv("MATRIX_HOMESERVER", "https://matrix.example.org")
        monkeypatch.setenv("MATRIX_DEVICE_ID", "HERMES_BOT")

        from gateway.config import GatewayConfig, _apply_env_overrides
        config = GatewayConfig()
        _apply_env_overrides(config)

        mc = config.platforms[Platform.MATRIX]
        assert mc.extra.get("device_id") == "HERMES_BOT"


class TestMatrixSyncLoop:


    @pytest.mark.asyncio
    async def test_dispatch_sync_accepts_async_handle_sync(self):
        """Some fake clients expose handle_sync as an async dispatcher."""
        adapter = _make_adapter()
        called = False

        async def handle_sync(sync_data):
            nonlocal called
            called = sync_data["next_batch"] == "s1"
            return []

        adapter._client = types.SimpleNamespace(handle_sync=handle_sync)

        await adapter._dispatch_sync({"next_batch": "s1"})

        assert called is True

    @pytest.mark.asyncio
    async def test_sync_loop_dispatches_registered_room_message_handler(self):
        """Inbound sync data should flow through handle_sync into message handling."""
        adapter = _make_adapter()
        adapter._closing = False
        adapter._user_id = "@bot:example.org"
        adapter._startup_ts = time.time() - 10
        adapter._dm_rooms = {"!dm:example.org": True}
        adapter._text_batch_delay_seconds = 0
        adapter._background_read_receipt = MagicMock()

        captured = []

        async def capture(event):
            captured.append(event)

        adapter.handle_message = capture

        event = types.SimpleNamespace(
            sender="@alice:example.org",
            event_id="$dm1",
            room_id="!dm:example.org",
            timestamp=int(time.time() * 1000),
            content={"msgtype": "m.text", "body": "hello"},
        )

        async def _sync_once(**kwargs):
            adapter._closing = True
            return {"rooms": {"join": {"!dm:example.org": {}}}, "next_batch": "s1234"}

        mock_sync_store = MagicMock()
        mock_sync_store.get_next_batch = AsyncMock(return_value=None)
        mock_sync_store.put_next_batch = AsyncMock()

        fake_client = MagicMock()
        fake_client.sync = AsyncMock(side_effect=_sync_once)
        fake_client.sync_store = mock_sync_store
        fake_client.get_state_event = AsyncMock(side_effect=Exception("no state"))
        fake_client.state_store = MagicMock()
        fake_client.state_store.get_members = AsyncMock(return_value=["@bot:example.org", "@alice:example.org"])
        fake_client.state_store.get_member = AsyncMock(return_value=None)

        def handle_sync(sync_data):
            return [asyncio.create_task(adapter._on_room_message(event))]

        fake_client.handle_sync = MagicMock(side_effect=handle_sync)
        adapter._client = fake_client

        await adapter._sync_loop()

        assert len(captured) == 1
        assert captured[0].text == "hello"
        assert captured[0].source.chat_type == "dm"

    @pytest.mark.asyncio
    async def test_connect_receives_dm_from_initial_sync_dispatch(self):
        """A DM delivered by initial sync should reach the message handler after connect."""
        from plugins.platforms.matrix.adapter import MatrixAdapter

        adapter = MatrixAdapter(
            PlatformConfig(
                enabled=True,
                token="syt_test_access_token",
                extra={
                    "homeserver": "https://matrix.example.org",
                    "user_id": "@bot:example.org",
                    "encryption": False,
                },
            )
        )
        adapter._text_batch_delay_seconds = 0
        adapter._background_read_receipt = MagicMock()

        captured = []

        async def capture(event):
            captured.append(event)

        adapter.handle_message = capture

        fake_mautrix_mods = _make_fake_mautrix()

        mock_sync_store = MagicMock()
        mock_sync_store.get_next_batch = AsyncMock(return_value=None)
        mock_sync_store.put_next_batch = AsyncMock()

        mock_client = MagicMock()
        mock_client.mxid = "@bot:example.org"
        mock_client.device_id = None
        mock_client.crypto = None
        mock_client.sync_store = mock_sync_store
        mock_client.whoami = AsyncMock(return_value=MagicMock(user_id="@bot:example.org", device_id="DEV123"))
        mock_client.sync = AsyncMock(return_value={
            "rooms": {"join": {"!dm:example.org": {}}},
            "next_batch": "s1",
        })
        mock_client.get_account_data = AsyncMock(
            return_value=MagicMock(content={"@alice:example.org": ["!dm:example.org"]})
        )
        mock_client.get_state_event = AsyncMock(side_effect=Exception("no state"))
        mock_client.state_store = MagicMock()
        mock_client.state_store.get_members = AsyncMock(return_value=["@bot:example.org", "@alice:example.org"])
        mock_client.state_store.get_member = AsyncMock(return_value=None)
        mock_client.add_event_handler = MagicMock()
        mock_client.add_dispatcher = MagicMock()
        mock_client.api = MagicMock()
        mock_client.api.token = "syt_test_access_token"
        mock_client.api.session = MagicMock()
        mock_client.api.session.close = AsyncMock()

        event = types.SimpleNamespace(
            sender="@alice:example.org",
            event_id="$initial-dm",
            room_id="!dm:example.org",
            timestamp=int(time.time() * 1000),
            content={"msgtype": "m.text", "body": "hello after connect"},
        )

        def handle_sync(sync_data):
            return [asyncio.create_task(adapter._on_room_message(event))]

        mock_client.handle_sync = MagicMock(side_effect=handle_sync)
        fake_mautrix_mods["mautrix.client"].Client = MagicMock(return_value=mock_client)

        import plugins.platforms.matrix.adapter as matrix_mod
        with patch.dict("sys.modules", fake_mautrix_mods):
            with patch.object(matrix_mod, "_create_matrix_session", return_value=MagicMock()):
                with patch.object(adapter, "_sync_loop", AsyncMock(return_value=None)):
                    assert await adapter.connect() is True

        assert len(captured) == 1
        assert captured[0].text == "hello after connect"
        assert captured[0].source.chat_type == "dm"

        await adapter.disconnect()


class TestMatrixUploadAndSend:

    @pytest.mark.asyncio
    async def test_upload_encrypted_room_uses_file_payload(self):
        """Encrypted rooms should use 'file' key with crypto metadata."""
        adapter = _make_adapter()
        adapter._encryption = True
        mock_client = MagicMock()
        mock_client.crypto = MagicMock()
        mock_client.crypto.crypto_store.get_outbound_group_session = AsyncMock(return_value=None)
        mock_client.state_store = MagicMock()
        mock_client.state_store.is_encrypted = AsyncMock(return_value=True)
        mock_client.upload_media = AsyncMock(return_value="mxc://example.org/enc")
        mock_client.send_message_event = AsyncMock(return_value="$event")
        adapter._client = mock_client

        with patch.dict("sys.modules", _make_fake_mautrix()):
            result = await adapter._upload_and_send(
                "!room:example.org", b"secret", "secret.txt", "text/plain", "m.file",
            )

        assert result.success is True
        # Should have uploaded ciphertext, not plaintext
        uploaded_data = mock_client.upload_media.await_args.args[0]
        assert uploaded_data != b"secret"
        sent = mock_client.send_message_event.await_args.args[2]
        assert "url" not in sent
        assert "file" in sent
        assert sent["file"]["url"] == "mxc://example.org/enc"


    @pytest.mark.asyncio
    async def test_media_preserves_caption_and_thread(self):
        adapter = _make_adapter()
        mock_client = MagicMock()
        mock_client.upload_media = AsyncMock(return_value="mxc://example.org/plain")
        mock_client.send_message_event = AsyncMock(return_value="$event")
        adapter._client = mock_client

        result = await adapter._upload_and_send(
            "!room:example.org",
            b"image",
            "chart.png",
            "image/png",
            "m.image",
            caption="Chart caption",
            metadata={"thread_id": "$root"},
        )

        assert result.success is True
        sent = mock_client.send_message_event.await_args.args[2]
        assert sent["body"] == "Chart caption"
        assert sent["m.relates_to"]["rel_type"] == "m.thread"
        assert sent["m.relates_to"]["event_id"] == "$root"
        assert sent["m.relates_to"]["m.in_reply_to"] == {"event_id": "$root"}


class TestMatrixDiagnostics:
    def test_diagnostics_redacts_credentials_and_reports_status(self, monkeypatch):
        import plugins.platforms.matrix.adapter as matrix_mod

        monkeypatch.setenv("MATRIX_RECOVERY_KEY", "secret recovery key")
        adapter = _make_adapter()
        adapter._access_token = "syt_super_secret"
        adapter._password = "password"
        adapter._user_id = "@bot:example.org"
        adapter._device_id = "DEV123"
        adapter._joined_rooms = {"!one:example.org", "!two:example.org"}
        adapter._last_sync_ts = time.time() - 7
        adapter._max_media_bytes = 123
        adapter._client = MagicMock()

        with patch.object(matrix_mod, "_check_e2ee_deps", return_value=True):
            diagnostics = adapter.get_diagnostics()

        assert diagnostics["auth"]["token_preview"] == "***"
        assert "syt_super_secret" not in str(diagnostics)
        assert "DEV123" not in str(diagnostics)
        assert diagnostics["auth"]["device_id_present"] is True
        assert diagnostics["auth"]["device_id_preview"] == "***"
        assert diagnostics["sync"]["connected"] is True
        assert diagnostics["sync"]["joined_room_count"] == 2
        assert diagnostics["sync"]["last_sync_age_seconds"] >= 0
        assert diagnostics["e2ee"]["recovery_key_configured"] is True
        assert diagnostics["media"]["max_media_bytes"] == 123


    @pytest.mark.asyncio
    async def test_matrix_recovery_key_bootstrap_skips_existing_output_file(
        self,
        tmp_path,
        monkeypatch,
        caplog,
    ):
        from plugins.platforms.matrix.adapter import MatrixAdapter

        output_path = tmp_path / "matrix-recovery-key.txt"
        output_path.write_text("existing\n")
        monkeypatch.delenv("MATRIX_RECOVERY_KEY", raising=False)
        monkeypatch.setenv("MATRIX_RECOVERY_KEY_OUTPUT_FILE", str(output_path))
        config = PlatformConfig(
            enabled=True,
            token="syt_test_token",
            extra={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
                "encryption": True,
            },
        )
        adapter = MatrixAdapter(config)
        fake_mautrix_mods = _make_fake_mautrix()

        mock_client = MagicMock()
        mock_client.mxid = "@bot:example.org"
        mock_client.device_id = None
        mock_client.state_store = MagicMock()
        mock_client.sync_store = MagicMock()
        mock_client.crypto = None
        mock_client.whoami = AsyncMock(return_value=MagicMock(user_id="@bot:example.org", device_id="DEV123"))
        mock_client.sync = AsyncMock(return_value={"rooms": {"join": {}}})
        mock_client.add_event_handler = MagicMock()
        mock_client.add_dispatcher = MagicMock()
        mock_client.handle_sync = MagicMock(return_value=[])
        mock_client.query_keys = AsyncMock(return_value={
            "device_keys": {"@bot:example.org": {"DEV123": {
                "keys": {"ed25519:DEV123": "fake_ed25519_key"},
            }}},
        })
        mock_client.api = MagicMock()
        mock_client.api.token = "syt_test_token"
        mock_client.api.session = MagicMock()
        mock_client.api.session.close = AsyncMock()

        mock_olm = MagicMock()
        mock_olm.load = AsyncMock()
        mock_olm.share_keys = AsyncMock()
        mock_olm.get_own_cross_signing_public_keys = AsyncMock(return_value=None)
        mock_olm.generate_recovery_key = AsyncMock(return_value="super-secret-key")
        mock_olm.share_keys_min_trust = None
        mock_olm.send_keys_min_trust = None
        mock_olm.account = MagicMock()
        mock_olm.account.identity_keys = {"ed25519": "fake_ed25519_key"}

        fake_mautrix_mods["mautrix.client"].Client = MagicMock(return_value=mock_client)
        fake_mautrix_mods["mautrix.crypto"].OlmMachine = MagicMock(return_value=mock_olm)

        import plugins.platforms.matrix.adapter as matrix_mod
        with patch.object(matrix_mod, "_check_e2ee_deps", return_value=True):
            with patch.dict("sys.modules", fake_mautrix_mods):
                with patch.object(adapter, "_refresh_dm_cache", AsyncMock()):
                    with patch.object(adapter, "_sync_loop", AsyncMock(return_value=None)):
                        assert await adapter.connect() is True

        mock_olm.generate_recovery_key.assert_not_called()
        assert "already exists" in caplog.text
        assert "super-secret-key" not in caplog.text
        assert output_path.read_text() == "existing\n"
        await adapter.disconnect()

    def test_matrix_diagnostics_redacts_recovery_key(self, monkeypatch):
        monkeypatch.setenv("MATRIX_RECOVERY_KEY", "diagnostic-secret-recovery-key")
        adapter = _make_adapter()

        diagnostics = adapter.get_diagnostics()

        assert diagnostics["e2ee"]["recovery_key_configured"] is True
        assert "diagnostic-secret-recovery-key" not in str(diagnostics)


class TestMatrixOwnDeviceSigning:
    def test_single_profile_recovery_key_falls_back_to_own_hermes_home(
        self, tmp_path, monkeypatch
    ):
        import plugins.platforms.matrix.adapter as matrix_mod

        (tmp_path / ".env").write_text('MATRIX_RECOVERY_KEY="profile-key"\n')
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr(matrix_mod, "get_secret", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(matrix_mod, "is_multiplex_active", lambda: False)

        assert matrix_mod._scoped_recovery_key() == "profile-key"

    @pytest.mark.asyncio
    async def test_fresh_own_device_is_fetched_before_cross_signing(self):
        from plugins.platforms.matrix.adapter import _get_own_device_for_signing

        device = MagicMock(device_id="NEWDEVICE")
        olm = MagicMock()
        olm.crypto_store.get_device = AsyncMock(return_value=None)
        olm.get_or_fetch_device = AsyncMock(return_value=device)

        result = await _get_own_device_for_signing(
            olm,
            "@bot:example.org",
            "NEWDEVICE",
        )

        assert result is device
        olm.crypto_store.get_device.assert_awaited_once_with(
            "@bot:example.org",
            "NEWDEVICE",
        )
        olm.get_or_fetch_device.assert_awaited_once_with(
            "@bot:example.org",
            "NEWDEVICE",
        )


class TestReliableMatrixOlmMachine:
    @pytest.mark.asyncio
    async def test_group_key_share_rekeys_one_device_when_session_is_missing(self):
        from mautrix.crypto.encrypt_megolm import key_missing
        from plugins.platforms.matrix.adapter import _make_reliable_olm_machine

        recovered = object()

        class BaseOlmMachine:
            def __init__(self):
                self.crypto_store = MagicMock()
                self.crypto_store.remove_outbound_group_session = AsyncMock()

            async def _find_olm_sessions(
                self, session, user_id, device_id, device
            ):
                return recovered

        machine = _make_reliable_olm_machine(BaseOlmMachine)()
        machine.send_encrypted_to_device = AsyncMock()
        machine._hermes_pending_key_shares = {"!room:example.org": set()}
        session = MagicMock(room_id="!room:example.org")
        device = MagicMock(device_id="LENADEVICE")

        result = await machine._find_olm_sessions(
            session,
            "@lena:example.org",
            "LENADEVICE",
            device,
        )

        assert result is recovered
        machine.send_encrypted_to_device.assert_awaited_once()
        call = machine.send_encrypted_to_device.await_args
        assert call.args[0] is device
        assert str(call.args[1]) == "m.dummy"
        assert call.kwargs == {"_force_recreate_session": True}
        assert machine._hermes_pending_key_shares["!room:example.org"] == set()

    @pytest.mark.asyncio
    async def test_group_key_share_rekeys_even_with_persisted_olm_session(self):
        from plugins.platforms.matrix.adapter import _make_reliable_olm_machine

        persisted = object()
        refreshed = object()

        class BaseOlmMachine:
            async def _find_olm_sessions(
                self, session, user_id, device_id, device
            ):
                return refreshed

        machine = _make_reliable_olm_machine(BaseOlmMachine)()
        machine.send_encrypted_to_device = AsyncMock()
        machine._hermes_pending_key_shares = {"!room:example.org": set()}
        session = MagicMock(room_id="!room:example.org")
        device = MagicMock(device_id="LENADEVICE")

        result = await machine._find_olm_sessions(
            session, "@lena:example.org", "LENADEVICE", device
        )

        assert result is refreshed
        machine.send_encrypted_to_device.assert_awaited_once()
        assert machine.send_encrypted_to_device.await_args.kwargs == {
            "_force_recreate_session": True
        }

    @pytest.mark.asyncio
    async def test_rekey_happens_before_base_marks_device_as_shared(self):
        from plugins.platforms.matrix.adapter import _make_reliable_olm_machine

        fresh_session = object()

        class BaseOlmMachine:
            def __init__(self):
                self.base_calls = 0

            async def _find_olm_sessions(
                self, session, user_id, device_id, device
            ):
                self.base_calls += 1
                key = (user_id, device_id)
                if key in session.users_shared_with:
                    return "already-shared"
                session.users_shared_with.add(key)
                return fresh_session, device

        machine = _make_reliable_olm_machine(BaseOlmMachine)()
        machine.send_encrypted_to_device = AsyncMock()
        machine._hermes_pending_key_shares = {"!room:example.org": set()}
        session = MagicMock(room_id="!room:example.org")
        session.users_shared_with = set()
        device = MagicMock(device_id="LENADEVICE")

        result = await machine._find_olm_sessions(
            session, "@lena:example.org", "LENADEVICE", device
        )

        assert result == (fresh_session, device)
        assert machine.base_calls == 1
        assert ("@lena:example.org", "LENADEVICE") in session.users_shared_with
        machine.send_encrypted_to_device.assert_awaited_once()

    def test_real_olm_class_is_instantiated_through_reliable_wrapper(self):
        from plugins.platforms.matrix.adapter import _create_reliable_olm_machine

        class BaseOlmMachine:
            def __init__(self, marker):
                self.marker = marker

        machine = _create_reliable_olm_machine(BaseOlmMachine, "expected")

        assert type(machine).__name__ == "ReliableBaseOlmMachine"
        assert machine.marker == "expected"

    @pytest.mark.asyncio
    async def test_missing_inbound_megolm_session_starts_recovery_and_reraises(self):
        from mautrix.errors import SessionNotFound
        from plugins.platforms.matrix.adapter import _make_reliable_olm_machine

        class BaseOlmMachine:
            async def decrypt_megolm_event(self, event):
                raise SessionNotFound("missing-session", "sender-key")

        reliable_cls = _make_reliable_olm_machine(BaseOlmMachine)
        machine = reliable_cls()
        machine._recover_missing_megolm_event = AsyncMock()
        encrypted_event = MagicMock()

        with pytest.raises(SessionNotFound):
            await machine.decrypt_megolm_event(encrypted_event)

        machine._recover_missing_megolm_event.assert_awaited_once_with(
            encrypted_event
        )

    @pytest.mark.asyncio
    async def test_recovery_failure_does_not_replace_session_not_found(self):
        from mautrix.errors import SessionNotFound
        from plugins.platforms.matrix.adapter import _make_reliable_olm_machine

        class BaseOlmMachine:
            async def decrypt_megolm_event(self, event):
                raise SessionNotFound("missing-session", "sender-key")

        reliable_cls = _make_reliable_olm_machine(BaseOlmMachine)
        machine = reliable_cls()
        machine.log = MagicMock()
        machine._recover_missing_megolm_event = AsyncMock(
            side_effect=RuntimeError("recovery failed")
        )

        with pytest.raises(SessionNotFound):
            await machine.decrypt_megolm_event(MagicMock())

        machine.log.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_inbound_recovery_rekeys_requests_and_deduplicates(self):
        from plugins.platforms.matrix.adapter import _make_reliable_olm_machine

        class BaseOlmMachine:
            pass

        reliable_cls = _make_reliable_olm_machine(BaseOlmMachine)
        machine = reliable_cls()
        machine.log = MagicMock()
        machine.client = MagicMock(device_id="LENDEVICE")
        machine.client.send_to_device = AsyncMock()
        target_device = MagicMock(device_id="OWNERDEVICE")
        machine.get_or_fetch_device_by_key = AsyncMock(return_value=target_device)
        machine.send_encrypted_to_device = AsyncMock()
        machine._queue_missing_megolm_retry = MagicMock()

        encrypted_event = MagicMock()
        encrypted_event.room_id = "!room:example.org"
        encrypted_event.event_id = "$encrypted"
        encrypted_event.sender = "@owner:example.org"
        encrypted_event.content.sender_key = "curve25519-key"
        encrypted_event.content.device_id = "OWNERDEVICE"
        encrypted_event.content.session_id = "missing-session"
        encrypted_event.content.algorithm = "m.megolm.v1.aes-sha2"

        await machine._recover_missing_megolm_event(encrypted_event)
        await machine._recover_missing_megolm_event(encrypted_event)

        machine.send_encrypted_to_device.assert_awaited_once()
        assert machine.send_encrypted_to_device.await_args.kwargs == {
            "_force_recreate_session": True
        }
        machine.client.send_to_device.assert_awaited_once()
        event_type, messages = machine.client.send_to_device.await_args.args
        assert str(event_type) == "m.room_key_request"
        request = messages["@owner:example.org"]["OWNERDEVICE"]
        assert str(request.action) == "request"
        assert request.requesting_device_id == "LENDEVICE"
        assert request.body.room_id == "!room:example.org"
        assert request.body.session_id == "missing-session"
        assert request.body.sender_key == "curve25519-key"
        assert machine._queue_missing_megolm_retry.call_count == 2

    @pytest.mark.asyncio
    async def test_missing_inbound_recovery_skips_events_without_device_metadata(self):
        from plugins.platforms.matrix.adapter import _make_reliable_olm_machine

        class BaseOlmMachine:
            pass

        reliable_cls = _make_reliable_olm_machine(BaseOlmMachine)
        machine = reliable_cls()
        machine.log = MagicMock()
        machine.client = MagicMock(device_id="LENDEVICE")
        machine.client.send_to_device = AsyncMock()
        machine.get_or_fetch_device_by_key = AsyncMock()
        machine._queue_missing_megolm_retry = MagicMock()

        encrypted_event = MagicMock()
        encrypted_event.room_id = "!room:example.org"
        encrypted_event.event_id = "$encrypted"
        encrypted_event.sender = "@owner:example.org"
        encrypted_event.content.sender_key = None
        encrypted_event.content.device_id = None
        encrypted_event.content.session_id = "missing-session"

        await machine._recover_missing_megolm_event(encrypted_event)

        machine._queue_missing_megolm_retry.assert_not_called()
        machine.get_or_fetch_device_by_key.assert_not_awaited()
        machine.client.send_to_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_inbound_queue_deduplicates_running_task(self):
        from plugins.platforms.matrix.adapter import _make_reliable_olm_machine

        class BaseOlmMachine:
            pass

        machine = _make_reliable_olm_machine(BaseOlmMachine)()
        release = asyncio.Event()

        async def wait_for_release(event):
            await release.wait()

        machine._retry_missing_megolm_event = AsyncMock(side_effect=wait_for_release)
        encrypted_event = MagicMock(event_id="$same-event")

        machine._queue_missing_megolm_retry(encrypted_event)
        first_task = machine._hermes_missing_megolm_events["$same-event"]
        machine._queue_missing_megolm_retry(encrypted_event)

        assert machine._hermes_missing_megolm_events["$same-event"] is first_task
        machine._retry_missing_megolm_event.assert_called_once_with(encrypted_event)

        first_task.cancel()
        await asyncio.gather(first_task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_missing_inbound_retry_times_out_without_dispatch(self):
        from plugins.platforms.matrix.adapter import _make_reliable_olm_machine

        class BaseOlmMachine:
            pass

        machine = _make_reliable_olm_machine(BaseOlmMachine)()
        machine.log = MagicMock()
        machine.crypto_store = MagicMock()
        machine.crypto_store.has_group_session = AsyncMock(return_value=False)
        machine.client = MagicMock()
        machine.client.dispatch_event = MagicMock(return_value=[])
        encrypted_event = MagicMock()
        encrypted_event.room_id = "!room:example.org"
        encrypted_event.event_id = "$timeout"
        encrypted_event.content.session_id = "missing-session"

        with patch(
            "plugins.platforms.matrix.adapter.asyncio.sleep", new=AsyncMock()
        ) as sleep_mock:
            await machine._retry_missing_megolm_event(encrypted_event)

        assert machine.crypto_store.has_group_session.await_count == 61
        assert sleep_mock.await_count == 60
        machine.client.dispatch_event.assert_not_called()
        assert "did not arrive within 60 seconds" in str(
            machine.log.warning.call_args.args[0]
        )

    @pytest.mark.asyncio
    async def test_missing_inbound_retry_dispatches_after_key_arrives(self):
        from plugins.platforms.matrix.adapter import _make_reliable_olm_machine

        decrypted_event = MagicMock()

        class BaseOlmMachine:
            async def decrypt_megolm_event(self, event):
                return decrypted_event

        reliable_cls = _make_reliable_olm_machine(BaseOlmMachine)
        machine = reliable_cls()
        machine.log = MagicMock()
        machine.crypto_store = MagicMock()
        machine.crypto_store.has_group_session = AsyncMock(return_value=True)
        machine.client = MagicMock()
        machine.client.dispatch_event = MagicMock(return_value=[])

        encrypted_event = MagicMock()
        encrypted_event.room_id = "!room:example.org"
        encrypted_event.event_id = "$encrypted"
        encrypted_event.source = "joined_room"
        encrypted_event.content.session_id = "missing-session"

        await machine._retry_missing_megolm_event(encrypted_event)

        machine.crypto_store.has_group_session.assert_awaited_once_with(
            "!room:example.org", "missing-session"
        )
        machine.client.dispatch_event.assert_called_once_with(
            decrypted_event, "joined_room"
        )

    @pytest.mark.asyncio
    async def test_decrypt_preserves_source_device_metadata(self):
        from plugins.platforms.matrix.adapter import _make_reliable_olm_machine

        class BaseOlmMachine:
            async def decrypt_megolm_event(self, event):
                return {"mautrix": {"was_encrypted": True}}

        encrypted_event = MagicMock()
        encrypted_event.content.device_id = "ACTIVEDEVICE"
        encrypted_event.content.sender_key = "curve25519-key"

        reliable_cls = _make_reliable_olm_machine(BaseOlmMachine)
        machine = reliable_cls()
        machine.get_or_fetch_device_by_key = AsyncMock(
            return_value=MagicMock(device_id="ACTIVEDEVICE")
        )
        result = await machine.decrypt_megolm_event(encrypted_event)

        assert result["mautrix"]["source_device_id"] == "ACTIVEDEVICE"
        assert result["mautrix"]["source_sender_key"] == "curve25519-key"

    @pytest.mark.asyncio
    async def test_decrypt_uses_device_bound_to_sender_key_not_event_device_id(self):
        from plugins.platforms.matrix.adapter import _make_reliable_olm_machine

        class BaseOlmMachine:
            async def decrypt_megolm_event(self, event):
                return {"mautrix": {"was_encrypted": True}}

        encrypted_event = MagicMock()
        encrypted_event.sender = "@alice:example.org"
        encrypted_event.content.device_id = "SPOOFEDDEVICE"
        encrypted_event.content.sender_key = "authenticated-curve25519-key"

        reliable_cls = _make_reliable_olm_machine(BaseOlmMachine)
        machine = reliable_cls()
        machine.get_or_fetch_device_by_key = AsyncMock(
            return_value=MagicMock(device_id="VERIFIEDDEVICE")
        )

        result = await machine.decrypt_megolm_event(encrypted_event)

        assert result["mautrix"]["source_device_id"] == "VERIFIEDDEVICE"
        assert result["mautrix"]["source_sender_key"] == (
            "authenticated-curve25519-key"
        )

    @pytest.mark.asyncio
    async def test_incomplete_group_share_fails_closed(self):
        from plugins.platforms.matrix.adapter import (
            _IncompleteMatrixKeyShare,
            _make_reliable_olm_machine,
        )

        class BaseOlmMachine:
            async def send_encrypted_to_device(self, *args, **kwargs):
                return None

            async def _find_olm_sessions(
                self, session, user_id, device_id, device
            ):
                from mautrix.crypto.encrypt_megolm import key_missing

                return key_missing

            async def _share_group_session(self, room_id, users):
                session = MagicMock(room_id=room_id)
                await self._find_olm_sessions(
                    session, users[0], "ALICEDEVICE", MagicMock()
                )

        session = MagicMock()
        session.users_shared_with = set()
        session.users_ignored = set()
        device = MagicMock()

        store = MagicMock()
        store.get_outbound_group_session = AsyncMock(return_value=session)
        store.get_devices = AsyncMock(return_value={"ALICEDEVICE": device})
        store.remove_outbound_group_session = AsyncMock()

        reliable_cls = _make_reliable_olm_machine(BaseOlmMachine)
        machine = reliable_cls()
        machine.crypto_store = store
        machine.client = MagicMock(mxid="@bot:example.org", device_id="BOTDEVICE")

        with pytest.raises(_IncompleteMatrixKeyShare):
            await machine._share_group_session(
                "!room:example.org", ["@alice:example.org"]
            )

        store.remove_outbound_group_session.assert_awaited_once_with(
            "!room:example.org"
        )

    @pytest.mark.asyncio
    async def test_persisted_session_without_transient_sets_does_not_false_fail(self):
        from plugins.platforms.matrix.adapter import _make_reliable_olm_machine

        class BaseOlmMachine:
            async def _share_group_session(self, room_id, users):
                return None

        persisted_session = MagicMock()
        persisted_session.users_shared_with = set()
        persisted_session.users_ignored = set()
        store = MagicMock()
        store.get_outbound_group_session = AsyncMock(return_value=persisted_session)
        store.get_devices = AsyncMock(
            return_value={"ALICEDEVICE": MagicMock()}
        )
        store.remove_outbound_group_session = AsyncMock()

        reliable_cls = _make_reliable_olm_machine(BaseOlmMachine)
        machine = reliable_cls()
        machine.crypto_store = store
        machine.client = MagicMock(mxid="@bot:example.org", device_id="BOTDEVICE")

        await machine._share_group_session(
            "!room:example.org", ["@alice:example.org"]
        )

        store.remove_outbound_group_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_then_created_olm_session_clears_share_failure(self):
        from plugins.platforms.matrix.adapter import _make_reliable_olm_machine

        class BaseOlmMachine:
            find_count = 0

            async def send_encrypted_to_device(self, *args, **kwargs):
                return None

            async def _find_olm_sessions(
                self, session, user_id, device_id, device
            ):
                from mautrix.crypto.encrypt_megolm import key_missing

                self.find_count += 1
                if self.find_count == 1:
                    return key_missing
                return MagicMock(), device

            async def _share_group_session(self, room_id, users):
                session = MagicMock(room_id=room_id)
                device = MagicMock()
                await self._find_olm_sessions(
                    session, users[0], "ALICEDEVICE", device
                )
                await self._find_olm_sessions(
                    session, users[0], "ALICEDEVICE", device
                )

        store = MagicMock()
        store.remove_outbound_group_session = AsyncMock()
        reliable_cls = _make_reliable_olm_machine(BaseOlmMachine)
        machine = reliable_cls()
        machine.crypto_store = store

        await machine._share_group_session(
            "!room:example.org", ["@alice:example.org"]
        )

        store.remove_outbound_group_session.assert_not_awaited()

    def test_records_last_active_encrypted_sender_device(self):
        adapter = _make_adapter()

        class Event:
            def __getitem__(self, key):
                assert key == "mautrix"
                return {
                    "was_encrypted": True,
                    "source_device_id": "ACTIVEDEVICE",
                    "source_sender_key": "curve25519-key",
                }

        adapter._remember_active_e2ee_device(
            "!room:example.org", "@alice:example.org", Event()
        )

        record = adapter._active_e2ee_devices["!room:example.org"]
        assert record.user_id == "@alice:example.org"
        assert record.device_id == "ACTIVEDEVICE"
        assert record.sender_key == "curve25519-key"

    @pytest.mark.asyncio
    async def test_room_message_intake_records_active_e2ee_device(self):
        adapter = _make_adapter()
        adapter._startup_ts = time.time() - 10
        adapter._remember_active_e2ee_device = MagicMock()
        event = MagicMock()
        event.room_id = "!room1:example.org"
        event.sender = "@alice:example.org"
        event.event_id = "$event1"
        event.timestamp = int(time.time() * 1000)
        event.content = {"body": "hello", "msgtype": "m.text"}

        await adapter._on_room_message(event)

        adapter._remember_active_e2ee_device.assert_called_once_with(
            "!room1:example.org", "@alice:example.org", event
        )

    @pytest.mark.asyncio
    async def test_without_active_device_skips_dm_resolution(self):
        adapter = _make_adapter()
        adapter._encryption = True
        adapter._client = MagicMock()
        adapter._client.crypto = MagicMock()
        adapter._client.crypto.crypto_store.get_outbound_group_session = AsyncMock(
            return_value=None
        )
        adapter._is_dm_room = AsyncMock(
            side_effect=AssertionError("DM resolution must not run")
        )

        await adapter._prepare_outbound_e2ee("!room:example.org")

        adapter._is_dm_room.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dm_resolution_failure_blocks_e2ee_preflight(self):
        from plugins.platforms.matrix.adapter import (
            _ActiveE2EEDevice,
            _IncompleteMatrixKeyShare,
        )

        adapter = _make_adapter()
        adapter._encryption = True
        adapter._active_e2ee_devices["!room:example.org"] = _ActiveE2EEDevice(
            user_id="@alice:example.org",
            device_id="ACTIVEDEVICE",
            sender_key="curve25519-key",
            seen_at=time.time(),
        )
        crypto_store = MagicMock()
        crypto_store.get_outbound_group_session = AsyncMock(return_value=None)
        adapter._client = MagicMock(
            crypto=MagicMock(crypto_store=crypto_store)
        )
        adapter._is_dm_room = AsyncMock(
            side_effect=RuntimeError("room identity unavailable")
        )

        with pytest.raises(_IncompleteMatrixKeyShare):
            await adapter._prepare_outbound_e2ee("!room:example.org")

    @pytest.mark.asyncio
    async def test_new_megolm_session_rekeys_recent_active_dm_device(self):
        from plugins.platforms.matrix.adapter import _ActiveE2EEDevice

        adapter = _make_adapter()
        adapter._encryption = True
        adapter._is_dm_room = AsyncMock(return_value=True)
        adapter._active_e2ee_devices["!room:example.org"] = _ActiveE2EEDevice(
            user_id="@alice:example.org",
            device_id="ACTIVEDEVICE",
            sender_key="curve25519-key",
            seen_at=time.time(),
        )

        device = MagicMock()
        device.device_id = "ACTIVEDEVICE"
        device.identity_key = "curve25519-key"
        crypto_store = MagicMock()
        crypto_store.get_outbound_group_session = AsyncMock(return_value=None)
        crypto_store.get_device = AsyncMock(return_value=device)
        crypto = MagicMock()
        crypto.crypto_store = crypto_store
        crypto.send_encrypted_to_device = AsyncMock()
        adapter._client = MagicMock()
        adapter._client.crypto = crypto

        await adapter._prepare_outbound_e2ee("!room:example.org")

        crypto.send_encrypted_to_device.assert_awaited_once()
        call = crypto.send_encrypted_to_device.await_args
        assert call.args[0] is device
        assert call.kwargs["_force_recreate_session"] is True

    @pytest.mark.asyncio
    async def test_persisted_shared_session_is_rekeyed_and_rotated_once(self):
        from plugins.platforms.matrix.adapter import _ActiveE2EEDevice

        adapter = _make_adapter()
        adapter._encryption = True
        adapter._is_dm_room = AsyncMock(return_value=True)
        adapter._active_e2ee_devices["!room:example.org"] = _ActiveE2EEDevice(
            user_id="@alice:example.org",
            device_id="ACTIVEDEVICE",
            sender_key="curve25519-key",
            seen_at=time.time(),
        )

        stale_session = MagicMock()
        stale_session.id = "stale-megolm-session"
        stale_session.shared = True
        stale_session.expired = False
        device = MagicMock(
            device_id="ACTIVEDEVICE", identity_key="curve25519-key"
        )
        crypto_store = MagicMock()
        crypto_store.get_outbound_group_session = AsyncMock(
            return_value=stale_session
        )
        crypto_store.get_device = AsyncMock(return_value=device)
        crypto_store.remove_outbound_group_session = AsyncMock()
        crypto = MagicMock()
        crypto.crypto_store = crypto_store
        crypto.send_encrypted_to_device = AsyncMock()
        adapter._client = MagicMock(crypto=crypto)

        await adapter._prepare_outbound_e2ee("!room:example.org")

        crypto.send_encrypted_to_device.assert_awaited_once()
        crypto_store.remove_outbound_group_session.assert_awaited_once_with(
            "!room:example.org"
        )

    @pytest.mark.asyncio
    async def test_group_room_rotates_session_without_active_inbound_device(self):
        adapter = _make_adapter()
        adapter._encryption = True
        adapter._is_dm_room = AsyncMock(
            side_effect=AssertionError("DM resolution must not run")
        )
        stale_session = MagicMock(shared=True, expired=False, message_count=32)
        crypto_store = MagicMock()
        crypto_store.get_outbound_group_session = AsyncMock(
            return_value=stale_session
        )
        crypto_store.remove_outbound_group_session = AsyncMock()
        adapter._client = MagicMock(crypto=MagicMock(crypto_store=crypto_store))

        await adapter._prepare_outbound_e2ee("!room:example.org")

        crypto_store.remove_outbound_group_session.assert_awaited_once_with(
            "!room:example.org"
        )
        adapter._is_dm_room.assert_not_awaited()


class TestMatrixEncryptedSendFallback:
    @pytest.mark.asyncio
    async def test_successful_send_marks_current_megolm_session_prepared(self):
        from plugins.platforms.matrix.adapter import _ActiveE2EEDevice

        adapter = _make_adapter()
        adapter._encryption = True
        active = _ActiveE2EEDevice(
            user_id="@alice:example.org",
            device_id="ACTIVEDEVICE",
            sender_key="curve25519-key",
            seen_at=time.time(),
        )
        adapter._prepare_outbound_e2ee = AsyncMock(return_value=active)
        adapter._active_e2ee_devices["!room:example.org"] = active
        current_session = MagicMock(
            id="healthy-megolm-session", shared=True, expired=False
        )
        crypto_store = MagicMock()
        crypto_store.get_outbound_group_session = AsyncMock(
            return_value=current_session
        )
        crypto = MagicMock(crypto_store=crypto_store)
        fake_client = MagicMock(crypto=crypto)
        fake_client.send_message_event = AsyncMock(return_value="$event")
        adapter._client = fake_client

        result = await adapter.send("!room:example.org", "hello")

        assert result.success is True
        assert adapter._prepared_e2ee_sessions[
            (
                "!room:example.org",
                "@alice:example.org",
                "ACTIVEDEVICE",
                "curve25519-key",
            )
        ] == "healthy-megolm-session"

    @pytest.mark.asyncio
    async def test_disconnect_cancels_megolm_recovery_tasks_and_clears_caches(self):
        from plugins.platforms.matrix.adapter import _ActiveE2EEDevice

        adapter = _make_adapter()
        retry_task = asyncio.create_task(asyncio.sleep(300))
        crypto = MagicMock()
        crypto._hermes_missing_megolm_events = {"$event": retry_task}
        client = MagicMock(crypto=crypto)
        client.api.session.close = AsyncMock()
        adapter._client = client
        adapter._active_e2ee_devices["!room:example.org"] = _ActiveE2EEDevice(
            user_id="@alice:example.org",
            device_id="ACTIVEDEVICE",
            sender_key="curve25519-key",
            seen_at=time.time(),
        )
        adapter._prepared_e2ee_sessions[("room", "user", "device", "key")] = (
            "session"
        )
        room_lock = asyncio.Lock()
        await room_lock.acquire()
        adapter._e2ee_send_locks["!room:example.org"] = room_lock

        await adapter.disconnect()

        assert retry_task.cancelled()
        assert crypto._hermes_missing_megolm_events == {}
        assert adapter._active_e2ee_devices == {}
        assert adapter._prepared_e2ee_sessions == {}
        assert adapter._e2ee_send_locks["!room:example.org"] is room_lock
        assert room_lock.locked()
        room_lock.release()

    @pytest.mark.asyncio
    async def test_disconnect_waits_for_inflight_connect_lifecycle(self):
        adapter = _make_adapter()
        connect_entered = asyncio.Event()
        release_connect = asyncio.Event()
        order = []

        async def connect_unlocked(*, is_reconnect=False):
            order.append("connect-enter")
            connect_entered.set()
            await release_connect.wait()
            order.append("connect-exit")
            return True

        async def disconnect_unlocked():
            order.append("disconnect")

        adapter._connect_unlocked = AsyncMock(side_effect=connect_unlocked)
        adapter._disconnect_unlocked = AsyncMock(side_effect=disconnect_unlocked)

        connect_task = asyncio.create_task(adapter.connect())
        await connect_entered.wait()
        disconnect_task = asyncio.create_task(adapter.disconnect())
        await asyncio.sleep(0)
        adapter._disconnect_unlocked.assert_not_awaited()

        release_connect.set()
        assert await connect_task is True
        await disconnect_task
        assert order == ["connect-enter", "connect-exit", "disconnect"]

    @pytest.mark.asyncio
    async def test_device_key_share_upload_has_bounded_timeout(self):
        adapter = _make_adapter()
        client = MagicMock(device_id="DEVICE", mxid="@bot:example.org")
        client.query_keys = AsyncMock(return_value=MagicMock(device_keys={}))
        olm = MagicMock()
        olm.account.identity_keys = {"ed25519": "local-key"}
        olm.account.shared = True
        olm.share_keys = AsyncMock()

        async def bounded_wait_for(awaitable, *, timeout):
            awaitable.close()
            raise asyncio.TimeoutError

        wait_for = AsyncMock(side_effect=bounded_wait_for)

        with patch(
            "plugins.platforms.matrix.adapter.asyncio.wait_for", new=wait_for
        ):
            result = await adapter._verify_device_keys_on_server(client, olm)

        assert result is False
        assert wait_for.await_args.kwargs["timeout"] == 30

    @pytest.mark.asyncio
    async def test_disconnect_cancels_send_before_reconnect_client_can_be_used(self):
        adapter = _make_adapter()
        adapter._encryption = True
        preflight_entered = asyncio.Event()
        release_preflight = asyncio.Event()

        async def prepare(*args, **kwargs):
            preflight_entered.set()
            await release_preflight.wait()
            return None

        adapter._prepare_outbound_e2ee = AsyncMock(side_effect=prepare)
        client_a = MagicMock(crypto=None)
        client_a.send_message_event = AsyncMock(return_value="$from-a")
        client_a.api.session.close = AsyncMock()
        adapter._client = client_a

        send_task = asyncio.create_task(
            adapter._send_reliable_event(
                "!room:example.org", "m.room.message", {}
            )
        )
        await preflight_entered.wait()
        await adapter.disconnect()

        client_b = MagicMock(crypto=None)
        client_b.send_message_event = AsyncMock(return_value="$from-b")
        adapter._client = client_b
        release_preflight.set()
        try:
            await send_task
        except asyncio.CancelledError:
            pass

        assert send_task.cancelled()
        client_a.send_message_event.assert_not_awaited()
        client_b.send_message_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_disconnect_cancels_text_retry_blocked_in_share_keys(self):
        adapter = _make_adapter()
        adapter._encryption = True
        adapter._prepare_outbound_e2ee = AsyncMock(return_value=None)
        share_entered = asyncio.Event()
        never_release = asyncio.Event()

        async def share_keys():
            share_entered.set()
            await never_release.wait()

        crypto = MagicMock()
        crypto.share_keys = AsyncMock(side_effect=share_keys)
        client = MagicMock(crypto=crypto)
        client.send_message_event = AsyncMock(side_effect=Exception("encryption error"))
        client.api.session.close = AsyncMock()
        adapter._client = client
        guard = MagicMock(returncode=0)
        guard.communicate = AsyncMock(return_value=(b"", b""))

        with patch(
            "plugins.platforms.matrix.adapter.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=guard),
        ):
            send_task = asyncio.create_task(
                adapter.send("!room:example.org", "hello")
            )
            await share_entered.wait()
            await asyncio.wait_for(adapter.disconnect(), timeout=1)

        cancelled_by_disconnect = send_task.cancelled()
        if not send_task.done():
            send_task.cancel()
        await asyncio.gather(send_task, return_exceptions=True)
        assert cancelled_by_disconnect

    @pytest.mark.asyncio
    async def test_persisted_shared_session_reaction_then_text_repairs_once(self):
        from plugins.platforms.matrix.adapter import _ActiveE2EEDevice

        adapter = _make_adapter()
        adapter._encryption = True
        adapter._is_dm_room = AsyncMock(return_value=True)
        adapter._active_e2ee_devices["!room:example.org"] = _ActiveE2EEDevice(
            user_id="@alice:example.org",
            device_id="ACTIVEDEVICE",
            sender_key="curve25519-key",
            seen_at=time.time(),
        )

        stale = MagicMock(id="stale-session", shared=True, expired=False)
        healthy = MagicMock(id="healthy-session", shared=True, expired=False)
        state = {"session": stale}
        crypto_store = MagicMock()
        crypto_store.get_outbound_group_session = AsyncMock(
            side_effect=lambda room_id: state["session"]
        )

        async def remove_session(room_id):
            state["session"] = None

        crypto_store.remove_outbound_group_session = AsyncMock(
            side_effect=remove_session
        )
        device = MagicMock(device_id="ACTIVEDEVICE")
        crypto_store.get_device = AsyncMock(return_value=device)
        crypto = MagicMock(crypto_store=crypto_store)
        crypto.get_or_fetch_device_by_key = AsyncMock(return_value=device)
        crypto.send_encrypted_to_device = AsyncMock()
        crypto.share_keys = AsyncMock()
        client = MagicMock(crypto=crypto)

        async def send_message_event(room_id, event_type, content):
            if str(event_type) != "m.reaction":
                state["session"] = healthy
            return "$event"

        client.send_message_event = AsyncMock(side_effect=send_message_event)
        client.state_store.is_encrypted = AsyncMock(return_value=True)
        adapter._client = client
        guard = MagicMock(returncode=0)
        guard.communicate = AsyncMock(return_value=(b"", b""))

        with patch(
            "plugins.platforms.matrix.adapter.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=guard),
        ):
            reaction_id = await adapter._send_reaction(
                "!room:example.org", "$incoming", "\U0001f440"
            )
            first = await adapter.send("!room:example.org", "first response")
            second = await adapter.send("!room:example.org", "second response")

        assert reaction_id == "$event"
        assert first.success is True
        assert second.success is True
        crypto.send_encrypted_to_device.assert_awaited_once()
        crypto_store.remove_outbound_group_session.assert_awaited_once_with(
            "!room:example.org"
        )
        crypto.share_keys.assert_not_awaited()
        assert adapter._prepared_e2ee_sessions[
            (
                "!room:example.org",
                "@alice:example.org",
                "ACTIVEDEVICE",
                "curve25519-key",
            )
        ] == "healthy-session"

    @pytest.mark.asyncio
    async def test_send_marks_the_device_snapshot_used_by_preflight(self):
        from plugins.platforms.matrix.adapter import _ActiveE2EEDevice

        adapter = _make_adapter()
        adapter._encryption = True
        device_a = _ActiveE2EEDevice(
            user_id="@alice:example.org",
            device_id="DEVICE_A",
            sender_key="sender-key-a",
            seen_at=time.time(),
        )
        device_b = _ActiveE2EEDevice(
            user_id="@alice:example.org",
            device_id="DEVICE_B",
            sender_key="sender-key-b",
            seen_at=time.time(),
        )
        adapter._active_e2ee_devices["!room:example.org"] = device_a
        adapter._prepare_outbound_e2ee = AsyncMock(return_value=device_a)
        session = MagicMock(id="healthy-session", shared=True, expired=False)
        crypto_store = MagicMock()
        crypto_store.get_outbound_group_session = AsyncMock(return_value=session)
        crypto = MagicMock(crypto_store=crypto_store)
        client = MagicMock(crypto=crypto)

        async def send_message_event(*args):
            adapter._active_e2ee_devices["!room:example.org"] = device_b
            return "$event"

        client.send_message_event = AsyncMock(side_effect=send_message_event)
        adapter._client = client

        await adapter._send_reliable_event(
            "!room:example.org", "m.room.message", {}
        )

        assert adapter._prepared_e2ee_sessions[
            (
                "!room:example.org",
                "@alice:example.org",
                "DEVICE_A",
                "sender-key-a",
            )
        ] == "healthy-session"
        assert (
            "!room:example.org",
            "@alice:example.org",
            "DEVICE_B",
            "sender-key-b",
        ) not in adapter._prepared_e2ee_sessions

    @pytest.mark.asyncio
    async def test_text_retry_marks_the_preflight_device_snapshot(self):
        from plugins.platforms.matrix.adapter import _ActiveE2EEDevice

        adapter = _make_adapter()
        adapter._encryption = True
        device_a = _ActiveE2EEDevice(
            user_id="@alice:example.org",
            device_id="DEVICE_A",
            sender_key="sender-key-a",
            seen_at=time.time(),
        )
        device_b = _ActiveE2EEDevice(
            user_id="@alice:example.org",
            device_id="DEVICE_B",
            sender_key="sender-key-b",
            seen_at=time.time(),
        )
        adapter._active_e2ee_devices["!room:example.org"] = device_a
        adapter._prepare_outbound_e2ee = AsyncMock(return_value=device_a)
        session = MagicMock(id="retry-session", shared=True, expired=False)
        crypto_store = MagicMock()
        crypto_store.get_outbound_group_session = AsyncMock(return_value=session)
        crypto = MagicMock(crypto_store=crypto_store)
        crypto.share_keys = AsyncMock()
        client = MagicMock(crypto=crypto)

        async def send_message_event(*args):
            if client.send_message_event.await_count == 1:
                adapter._active_e2ee_devices["!room:example.org"] = device_b
                raise Exception("encryption error")
            return "$retry-event"

        client.send_message_event = AsyncMock(side_effect=send_message_event)
        client.api.session.close = AsyncMock()
        adapter._client = client
        guard = MagicMock(returncode=0)
        guard.communicate = AsyncMock(return_value=(b"", b""))

        with patch(
            "plugins.platforms.matrix.adapter.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=guard),
        ):
            result = await adapter.send("!room:example.org", "hello")

        assert result.success is True
        assert adapter._prepared_e2ee_sessions[
            (
                "!room:example.org",
                "@alice:example.org",
                "DEVICE_A",
                "sender-key-a",
            )
        ] == "retry-session"
        assert (
            "!room:example.org",
            "@alice:example.org",
            "DEVICE_B",
            "sender-key-b",
        ) not in adapter._prepared_e2ee_sessions

    @pytest.mark.asyncio
    async def test_reliable_text_sends_are_serialized_per_room(self):
        adapter = _make_adapter()
        adapter._client = MagicMock()
        first_entered = asyncio.Event()
        release = asyncio.Event()
        active = 0
        max_active = 0

        async def send_text_unlocked(*args, **kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            first_entered.set()
            try:
                await release.wait()
                return MagicMock(success=True)
            finally:
                active -= 1

        adapter._send_text_unlocked = AsyncMock(side_effect=send_text_unlocked)

        first = asyncio.create_task(
            adapter._send_reliable_text("!room:example.org", "first", None, None)
        )
        try:
            await first_entered.wait()
            second = asyncio.create_task(
                adapter._send_reliable_text("!room:example.org", "second", None, None)
            )
            await asyncio.sleep(0.01)
            assert max_active == 1
        finally:
            release.set()
            if "second" in locals():
                await asyncio.gather(first, second)
            else:
                first.cancel()
                await asyncio.gather(first, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_reliable_events_are_serialized_per_room(self):
        adapter = _make_adapter()
        adapter._prepare_outbound_e2ee = AsyncMock()
        adapter._mark_outbound_e2ee_prepared = AsyncMock()
        first_entered = asyncio.Event()
        release = asyncio.Event()
        active = 0
        max_active = 0

        async def send_message_event(*args):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            first_entered.set()
            try:
                await release.wait()
                return "$event"
            finally:
                active -= 1

        client = MagicMock()
        client.send_message_event = AsyncMock(side_effect=send_message_event)
        adapter._client = client

        first = asyncio.create_task(
            adapter._send_reliable_event("!room:example.org", "m.room.message", {})
        )
        await first_entered.wait()
        second = asyncio.create_task(
            adapter._send_reliable_event("!room:example.org", "m.room.message", {})
        )
        await asyncio.sleep(0.01)
        try:
            assert max_active == 1
        finally:
            release.set()
            await asyncio.gather(first, second)

    @pytest.mark.asyncio
    async def test_media_event_fails_closed_when_e2ee_preflight_fails(self):
        adapter = _make_adapter()
        adapter._encryption = False
        adapter._send_reliable_event = AsyncMock(
            side_effect=RuntimeError("targeted E2EE rekey failed")
        )
        client = MagicMock()
        client.upload_media = AsyncMock(return_value="mxc://example.org/file")
        client.send_message_event = AsyncMock(return_value="$must-not-send")
        adapter._client = client

        result = await adapter._upload_and_send(
            "!room:example.org",
            b"content",
            "file.txt",
            "text/plain",
            "m.file",
        )

        assert result.success is False
        assert "targeted E2EE rekey failed" in result.error
        client.send_message_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_simple_message_fails_closed_when_e2ee_preflight_fails(self):
        adapter = _make_adapter()
        adapter._send_reliable_event = AsyncMock(
            side_effect=RuntimeError("targeted E2EE rekey failed")
        )
        client = MagicMock()
        client.send_message_event = AsyncMock(return_value="$must-not-send")
        adapter._client = client

        result = await adapter._send_simple_message(
            "!room:example.org", "notice", "m.notice"
        )

        assert result.success is False
        assert "targeted E2EE rekey failed" in result.error
        client.send_message_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_edit_fails_closed_when_e2ee_preflight_fails(self):
        adapter = _make_adapter()
        adapter._prepare_outbound_e2ee = AsyncMock(
            side_effect=RuntimeError("targeted E2EE rekey failed")
        )
        client = MagicMock()
        client.send_message_event = AsyncMock(return_value="$must-not-send")
        adapter._client = client

        result = await adapter.edit_message(
            "!room:example.org", "$original", "updated"
        )

        assert result.success is False
        assert "targeted E2EE rekey failed" in result.error
        client.send_message_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_fails_closed_when_targeted_e2ee_rekey_fails(self):
        adapter = _make_adapter()
        adapter._prepare_outbound_e2ee = AsyncMock(
            side_effect=RuntimeError("targeted E2EE rekey failed")
        )
        fake_client = MagicMock()
        fake_client.send_message_event = AsyncMock(return_value="$must-not-send")
        adapter._client = fake_client

        result = await adapter.send("!room:example.org", "hello")

        assert result.success is False
        assert "targeted E2EE rekey failed" in result.error
        fake_client.send_message_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_retries_after_e2ee_error(self):
        """send() should retry with crypto.share_keys() on E2EE errors."""
        adapter = _make_adapter()
        adapter._encryption = True

        fake_client = MagicMock()
        fake_client.send_message_event = AsyncMock(side_effect=[
            Exception("encryption error"),
            "$event123",  # mautrix returns EventID string directly
        ])
        mock_crypto = MagicMock()
        mock_crypto.share_keys = AsyncMock()
        mock_crypto.crypto_store.get_outbound_group_session = AsyncMock(return_value=None)
        fake_client.crypto = mock_crypto
        adapter._client = fake_client

        result = await adapter.send("!room:example.org", "hello")

        assert result.success is True
        assert result.message_id == "$event123"
        mock_crypto.share_keys.assert_awaited_once()
        assert fake_client.send_message_event.await_count == 2


# ---------------------------------------------------------------------------
# E2EE: _joined_rooms reference preservation for CryptoStateStore
# ---------------------------------------------------------------------------

class TestJoinedRoomsReference:
    def test_joined_rooms_reference_preserved_after_reassignment(self):
        """_CryptoStateStore must see updates after initial sync populates rooms."""
        from plugins.platforms.matrix.adapter import _CryptoStateStore

        joined = set()
        store = _CryptoStateStore(MagicMock(), joined)

        # Simulate what connect() should do: mutate in place, not reassign.
        joined.clear()
        joined.update(["!room1:example.org", "!room2:example.org"])

        import asyncio
        rooms = asyncio.get_event_loop().run_until_complete(store.find_shared_rooms("@user:ex"))
        assert set(rooms) == {"!room1:example.org", "!room2:example.org"}


# ---------------------------------------------------------------------------
# E2EE: connect registers encrypted event handler
# ---------------------------------------------------------------------------

class TestMatrixEncryptedEventHandler:
    @pytest.mark.asyncio
    async def test_connect_registers_encrypted_event_handler_when_encryption_on(self):
        from plugins.platforms.matrix.adapter import MatrixAdapter

        config = PlatformConfig(
            enabled=True,
            token="syt_test_token",
            extra={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
                "encryption": True,
            },
        )
        adapter = MatrixAdapter(config)

        fake_mautrix_mods = _make_fake_mautrix()

        mock_client = MagicMock()
        mock_client.mxid = "@bot:example.org"
        mock_client.device_id = None
        mock_client.state_store = MagicMock()
        mock_client.sync_store = MagicMock()
        mock_client.crypto = None  # Will be set during connect
        mock_client.whoami = AsyncMock(return_value=MagicMock(user_id="@bot:example.org", device_id="DEV123"))
        mock_client.sync = AsyncMock(return_value={"rooms": {"join": {"!room:server": {}}}})
        mock_client.add_event_handler = MagicMock()
        mock_client.handle_sync = MagicMock(return_value=[])
        mock_client.query_keys = AsyncMock(return_value={
            "device_keys": {"@bot:example.org": {"DEV123": {
                "keys": {"ed25519:DEV123": "fake_ed25519_key"},
            }}},
        })
        mock_client.api = MagicMock()
        mock_client.api.token = "syt_test_token"
        mock_client.api.session = MagicMock()
        mock_client.api.session.close = AsyncMock()

        mock_olm = MagicMock()
        mock_olm.load = AsyncMock()
        mock_olm.share_keys = AsyncMock()
        mock_olm.share_keys_min_trust = None
        mock_olm.send_keys_min_trust = None
        mock_olm.account = MagicMock()
        mock_olm.account.identity_keys = {"ed25519": "fake_ed25519_key"}

        fake_mautrix_mods["mautrix.client"].Client = MagicMock(return_value=mock_client)
        fake_mautrix_mods["mautrix.crypto"].OlmMachine = MagicMock(return_value=mock_olm)

        import plugins.platforms.matrix.adapter as matrix_mod
        with patch.object(matrix_mod, "_check_e2ee_deps", return_value=True):
            with patch.dict("sys.modules", fake_mautrix_mods):
                with patch.object(adapter, "_refresh_dm_cache", AsyncMock()):
                    with patch.object(adapter, "_sync_loop", AsyncMock(return_value=None)):
                        assert await adapter.connect() is True

        # Verify inbound event handlers were registered as sync-awaited
        # callbacks. mautrix only returns waited handler tasks from
        # handle_sync(), so background-only handlers leave _dispatch_sync()
        # without a completion point for Hermes' Matrix intake.
        handler_calls = mock_client.add_event_handler.call_args_list
        waited_types = {
            str(call.args[0])
            for call in handler_calls
            if call.kwargs.get("wait_sync") is True
        }

        assert "m.room.message" in waited_types
        assert "m.reaction" in waited_types
        assert "internal.invite" in waited_types

        await adapter.disconnect()


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------

class TestMatrixDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_closes_api_session(self):
        """disconnect() should close client.api.session."""
        adapter = _make_adapter()
        adapter._sync_task = None

        mock_session = MagicMock()
        mock_session.close = AsyncMock()

        mock_api = MagicMock()
        mock_api.session = mock_session

        fake_client = MagicMock()
        fake_client.api = mock_api
        adapter._client = fake_client

        await adapter.disconnect()

        mock_session.close.assert_awaited_once()
        assert adapter._client is None


# ---------------------------------------------------------------------------
# Markdown to HTML: security tests
# ---------------------------------------------------------------------------

class TestMatrixMarkdownHtmlSecurity:
    """Tests for HTML injection prevention in _markdown_to_html_fallback."""

    def setup_method(self):
        from plugins.platforms.matrix.adapter import MatrixAdapter
        self.convert = MatrixAdapter._markdown_to_html_fallback

    def test_script_injection_in_header(self):
        result = self.convert("# <script>alert(1)</script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_script_injection_in_plain_text(self):
        result = self.convert("Hello <script>alert(1)</script>")
        assert "<script>" not in result


    def test_link_text_html_injection(self):
        result = self.convert('[<img onerror="x">](http://safe.com)')
        assert "<img" not in result or "&lt;img" in result


    def test_html_injection_in_bold(self):
        result = self.convert("**<img onerror=alert(1)>**")
        assert "<img" not in result or "&lt;img" in result

    def test_html_injection_in_italic(self):
        result = self.convert("*<script>alert(1)</script>*")
        assert "<script>" not in result


# ---------------------------------------------------------------------------
# Markdown to HTML: extended formatting tests
# ---------------------------------------------------------------------------

class TestMatrixMarkdownHtmlFormatting:
    """Tests for new formatting capabilities in _markdown_to_html_fallback."""

    def setup_method(self):
        from plugins.platforms.matrix.adapter import MatrixAdapter
        self.convert = MatrixAdapter._markdown_to_html_fallback

    def test_fenced_code_block(self):
        result = self.convert('```python\ndef hello():\n    pass\n```')
        assert "<pre><code" in result
        assert "language-python" in result


    def test_code_block_html_escaped(self):
        result = self.convert('```\n<script>alert(1)</script>\n```')
        assert "&lt;script&gt;" in result
        assert "<script>" not in result

    def test_headers(self):
        assert "<h1>" in self.convert("# H1")
        assert "<h2>" in self.convert("## H2")
        assert "<h3>" in self.convert("### H3")

    def test_unordered_list(self):
        result = self.convert("- One\n- Two\n- Three")
        assert "<ul>" in result
        assert result.count("<li>") == 3


# ---------------------------------------------------------------------------
# Link URL sanitization
# ---------------------------------------------------------------------------

class TestMatrixLinkSanitization:
    def test_safe_https_url(self):
        from plugins.platforms.matrix.adapter import MatrixAdapter
        assert MatrixAdapter._sanitize_link_url("https://example.com") == "https://example.com"


    def test_quotes_escaped(self):
        from plugins.platforms.matrix.adapter import MatrixAdapter
        result = MatrixAdapter._sanitize_link_url('http://x"y')
        assert '"' not in result
        assert "&quot;" in result


# ---------------------------------------------------------------------------
# Reactions
# ---------------------------------------------------------------------------

class TestMatrixReactions:
    def setup_method(self):
        self.adapter = _make_adapter()

    @pytest.mark.asyncio
    async def test_send_reaction(self):
        """_send_reaction should call send_message_event with m.reaction."""
        mock_client = MagicMock()
        # mautrix send_message_event returns EventID string directly
        mock_client.send_message_event = AsyncMock(return_value="$reaction1")
        self.adapter._client = mock_client

        result = await self.adapter._send_reaction("!room:ex", "$event1", "\U0001f44d")
        assert result == "$reaction1"
        mock_client.send_message_event.assert_called_once()
        call_args = mock_client.send_message_event.call_args
        content = call_args.args[2] if len(call_args.args) > 2 else call_args.kwargs.get("content")
        assert content["m.relates_to"]["rel_type"] == "m.annotation"
        assert content["m.relates_to"]["key"] == "\U0001f44d"

    @pytest.mark.asyncio
    async def test_reaction_skips_e2ee_preflight_because_mautrix_does_not_encrypt_it(
        self,
    ):
        self.adapter._send_reliable_event = AsyncMock(
            side_effect=RuntimeError("must not run for an unencrypted reaction")
        )
        client = MagicMock()
        client.send_message_event = AsyncMock(return_value="$reaction")
        self.adapter._client = client

        result = await self.adapter._send_reaction(
            "!room:ex", "$event1", "\U0001f44d"
        )

        assert result == "$reaction"
        self.adapter._send_reliable_event.assert_not_awaited()
        client.send_message_event.assert_awaited_once()


    @pytest.mark.asyncio
    async def test_on_processing_complete_sends_check(self):
        from gateway.platforms.base import MessageEvent, MessageType, ProcessingOutcome

        self.adapter._reactions_enabled = True
        self.adapter._reaction_redaction_delay_seconds = 0.01
        self.adapter._pending_reactions = {("!room:ex", "$msg1"): "$eyes_reaction_123"}
        self.adapter._redact_reaction = AsyncMock(return_value=True)
        self.adapter._send_reaction = AsyncMock(return_value="$check_reaction_456")

        source = MagicMock()
        source.chat_id = "!room:ex"
        event = MessageEvent(
            text="hello",
            message_type=MessageType.TEXT,
            source=source,
            raw_message={},
            message_id="$msg1",
        )
        await self.adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        self.adapter._redact_reaction.assert_not_awaited()
        self.adapter._send_reaction.assert_called_once_with("!room:ex", "$msg1", "\u2705")
        await asyncio.sleep(0.03)
        self.adapter._redact_reaction.assert_awaited_once_with(
            "!room:ex",
            "$eyes_reaction_123",
            "processing complete",
        )


    @pytest.mark.asyncio
    async def test_approval_reaction_cleanup_is_delayed(self):
        """Bot approval reaction redactions should not run inline."""

        self.adapter._reaction_redaction_delay_seconds = 0.01
        self.adapter._redact_reaction = AsyncMock(return_value=True)
        prompt = MagicMock()
        prompt.bot_reaction_events = {
            "\u2705": "$allow_reaction",
            "\u274e": "$deny_reaction",
        }

        await self.adapter._redact_bot_approval_reactions("!room:ex", prompt)

        self.adapter._redact_reaction.assert_not_awaited()
        await asyncio.sleep(0.03)
        self.adapter._redact_reaction.assert_any_await(
            "!room:ex",
            "$allow_reaction",
            "approval resolved",
        )
        self.adapter._redact_reaction.assert_any_await(
            "!room:ex",
            "$deny_reaction",
            "approval resolved",
        )


# ---------------------------------------------------------------------------
# Read receipts
# ---------------------------------------------------------------------------

class TestMatrixReadReceipts:
    def setup_method(self):
        self.adapter = _make_adapter()


    @pytest.mark.asyncio
    async def test_send_read_receipt(self):
        """send_read_receipt should call mautrix's real read-marker API."""
        mock_client = MagicMock()
        mock_client.set_fully_read_marker = AsyncMock(return_value=None)
        self.adapter._client = mock_client

        result = await self.adapter.send_read_receipt("!room:ex", "$event1")
        assert result is True
        mock_client.set_fully_read_marker.assert_awaited_once_with(
            "!room:ex", "$event1", "$event1"
        )


# ---------------------------------------------------------------------------
# Media normalization
# ---------------------------------------------------------------------------

class TestMatrixImageOnlyMediaNormalization:
    def setup_method(self):
        self.adapter = _make_adapter()
        self.adapter._client = MagicMock()
        self.adapter._client.download_media = AsyncMock(return_value=None)
        self.adapter._is_dm_room = AsyncMock(return_value=True)
        self.adapter._get_display_name = AsyncMock(return_value="Alice")
        self.adapter._background_read_receipt = MagicMock()
        self.adapter._mxc_to_http = (
            lambda url: "https://matrix.example.org/_matrix/media/v3/download/example/30.png"
        )

    @pytest.mark.asyncio
    async def test_image_only_filename_body_is_not_forwarded_as_text(self):
        captured_event = None

        async def capture(msg_event):
            nonlocal captured_event
            captured_event = msg_event

        self.adapter.handle_message = capture

        await self.adapter._handle_media_message(
            room_id="!room:example.org",
            sender="@alice:example.org",
            event_id="$image1",
            event_ts=0.0,
            source_content={
                "msgtype": "m.image",
                "body": "30.png",
                "url": "mxc://example/30.png",
                "info": {"mimetype": "image/png"},
            },
            relates_to={},
            msgtype="m.image",
        )

        assert captured_event is not None
        assert captured_event.text == ""
        assert captured_event.media_urls == [
            "https://matrix.example.org/_matrix/media/v3/download/example/30.png"
        ]
        assert captured_event.message_type == MessageType.PHOTO


    @pytest.mark.asyncio
    async def test_inbound_oversized_media_is_rejected(self):
        captured_event = None

        async def capture(msg_event):
            nonlocal captured_event
            captured_event = msg_event

        self.adapter._max_media_bytes = 10
        self.adapter.handle_message = capture

        await self.adapter._handle_media_message(
            room_id="!room:example.org",
            sender="@alice:example.org",
            event_id="$image-big",
            event_ts=0.0,
            source_content={
                "msgtype": "m.image",
                "body": "huge.png",
                "url": "mxc://example/huge.png",
                "info": {"mimetype": "image/png", "size": 11},
            },
            relates_to={},
            msgtype="m.image",
        )

        assert captured_event is None
        self.adapter._client.download_media.assert_not_called()


    @pytest.mark.asyncio
    async def test_external_media_download_follows_safe_redirect(self, monkeypatch):
        """A redirect to another allowed URL is followed and its body returned."""
        import aiohttp
        import tools.url_safety as url_safety

        class _Content:
            async def iter_chunked(self, _size):
                yield b"imgbytes"

        class _RedirectResponse:
            status = 302
            headers = {"Location": "https://cdn.example.com/final.png"}
            content_type = "image/png"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            def raise_for_status(self):
                return None

        class _OkResponse:
            status = 200
            headers = {}
            content_type = "image/png"
            content = _Content()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            def raise_for_status(self):
                return None

        class _Session:
            def __init__(self):
                self.requested = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            def get(self, url, *_args, **_kwargs):
                self.requested.append(url)
                return _RedirectResponse() if len(self.requested) == 1 else _OkResponse()

        session = _Session()
        monkeypatch.setattr(aiohttp, "ClientSession", lambda **_kwargs: session)
        monkeypatch.setattr(url_safety, "is_safe_url", lambda *_args, **_kwargs: True)

        data, ct, _fname = await self.adapter._download_external_media_with_cap(
            "https://example.com/image.png"
        )

        assert data == b"imgbytes"
        assert ct == "image/png"
        assert session.requested == [
            "https://example.com/image.png",
            "https://cdn.example.com/final.png",
        ]


    @pytest.mark.asyncio
    async def test_send_image_failure_log_redacts_signed_url(self, caplog, monkeypatch):
        from gateway.platforms.base import SendResult
        import tools.url_safety as url_safety

        signed_url = "https://example.com/image.png?signature=secret-token#frag"
        self.adapter._download_external_media_with_cap = AsyncMock(
            side_effect=ValueError("download failed")
        )
        self.adapter.send = AsyncMock(return_value=SendResult(success=True))
        monkeypatch.setattr(url_safety, "is_safe_url", lambda *_args, **_kwargs: True)

        await self.adapter.send_image("!room:example.org", signed_url)

        assert "https://example.com/image.png" in caplog.text
        assert "secret-token" not in caplog.text
        assert "#frag" not in caplog.text


    @pytest.mark.asyncio
    async def test_send_image_failure_response_preserves_caption(self, monkeypatch):
        from gateway.platforms.base import SendResult
        import tools.url_safety as url_safety

        signed_url = "https://example.com/image.png?signature=secret-token#fragment"
        self.adapter._download_external_media_with_cap = AsyncMock(
            side_effect=ValueError("download failed")
        )
        self.adapter.send = AsyncMock(return_value=SendResult(success=True))
        monkeypatch.setattr(url_safety, "is_safe_url", lambda *_args, **_kwargs: True)

        await self.adapter.send_image(
            "!room:example.org",
            signed_url,
            caption="Here is the image",
        )

        sent_text = self.adapter.send.await_args.args[1]
        assert "Here is the image" in sent_text
        assert "signature=" not in sent_text
        assert "secret-token" not in sent_text
        assert "#fragment" not in sent_text
        assert signed_url not in sent_text

    @pytest.mark.asyncio
    async def test_send_image_failure_log_still_redacts_signed_url(self, caplog, monkeypatch):
        from gateway.platforms.base import SendResult
        import tools.url_safety as url_safety

        signed_url = "https://example.com/image.png?signature=secret-token#fragment"
        self.adapter._download_external_media_with_cap = AsyncMock(
            side_effect=ValueError("download failed")
        )
        self.adapter.send = AsyncMock(return_value=SendResult(success=True))
        monkeypatch.setattr(url_safety, "is_safe_url", lambda *_args, **_kwargs: True)

        await self.adapter.send_image("!room:example.org", signed_url)

        assert "https://example.com/image.png" in caplog.text
        assert "signature=" not in caplog.text
        assert "secret-token" not in caplog.text
        assert "#fragment" not in caplog.text


# ---------------------------------------------------------------------------
# Message redaction
# ---------------------------------------------------------------------------

class TestMatrixRedaction:
    def setup_method(self):
        self.adapter = _make_adapter()

    @pytest.mark.asyncio
    async def test_redact_message(self):
        """redact_message should call client.redact()."""
        mock_client = MagicMock()
        # mautrix redact() returns EventID string
        mock_client.redact = AsyncMock(return_value="$redact_event")
        self.adapter._client = mock_client

        result = await self.adapter.redact_message("!room:ex", "$ev1", "oops")
        assert result is True
        mock_client.redact.assert_called_once()

    @pytest.mark.asyncio
    async def test_redact_no_client(self):
        self.adapter._client = None
        result = await self.adapter.redact_message("!room:ex", "$ev1")
        assert result is False


# ---------------------------------------------------------------------------
# Room creation & invite
# ---------------------------------------------------------------------------

class TestMatrixRoomManagement:
    def setup_method(self):
        self.adapter = _make_adapter()

    @pytest.mark.asyncio
    async def test_create_room(self):
        """create_room should call client.create_room() returning RoomID string."""
        mock_client = MagicMock()
        # mautrix create_room returns RoomID string directly
        mock_client.create_room = AsyncMock(return_value="!new:example.org")
        self.adapter._client = mock_client

        room_id = await self.adapter.create_room(name="Test Room", topic="A test")
        assert room_id == "!new:example.org"
        assert "!new:example.org" in self.adapter._joined_rooms


# ---------------------------------------------------------------------------
# Presence
# ---------------------------------------------------------------------------

class TestMatrixPresence:
    def setup_method(self):
        self.adapter = _make_adapter()

    @pytest.mark.asyncio
    async def test_set_presence_valid(self):
        mock_client = MagicMock()
        mock_client.set_presence = AsyncMock()
        self.adapter._client = mock_client

        result = await self.adapter.set_presence("online")
        assert result is True


# ---------------------------------------------------------------------------
# Self / bridge / system sender filtering — regression coverage for #15763
# ("Hall of Mirrors": recursive pairing / echo loops triggered by bridge
# or bot-self senders bypassing the early-drop guard in _on_room_message).
# ---------------------------------------------------------------------------

class TestMatrixSelfSenderFilter:
    def setup_method(self):
        self.adapter = _make_adapter()

    def test_exact_match_is_self(self):
        self.adapter._user_id = "@bot:example.org"
        assert self.adapter._is_self_sender("@bot:example.org") is True

    def test_case_insensitive_match_is_self(self):
        # Some homeservers canonicalize the localpart differently at
        # different API surfaces — a case-sensitive equality check lets
        # the bot's own sender through and triggers the pairing / echo
        # loop in #15763.
        self.adapter._user_id = "@Bot:Example.ORG"
        assert self.adapter._is_self_sender("@bot:example.org") is True
        assert self.adapter._is_self_sender("@BOT:EXAMPLE.ORG") is True


class TestMatrixSystemBridgeFilter:
    def setup_method(self):
        self.adapter = _make_adapter()

    def test_appservice_underscore_prefix_is_bridge(self):
        # Conventional appservice namespace puppets
        assert self.adapter._is_system_or_bridge_sender(
            "@_telegram_12345:bridge.example.org"
        ) is True
        assert self.adapter._is_system_or_bridge_sender(
            "@_discord_999:example.org"
        ) is True
        assert self.adapter._is_system_or_bridge_sender(
            "@_slackbridge_puppet:example.org"
        ) is True


    def test_empty_sender_is_system(self):
        assert self.adapter._is_system_or_bridge_sender("") is True
        assert self.adapter._is_system_or_bridge_sender("   ") is True


class TestMatrixOnRoomMessageFilter:
    """End-to-end coverage of _on_room_message drop conditions."""

    def setup_method(self):
        self.adapter = _make_adapter()
        self.adapter._user_id = "@bot:example.org"
        self.adapter._startup_ts = 0.0  # accept any event_ts
        self.adapter._handle_text_message = AsyncMock()
        self.adapter._handle_media_message = AsyncMock()

    @staticmethod
    def _mk_event(sender, body="hi", msgtype="m.text", event_id=None, ts=None, room_id=None):
        import time as _t

        ev = MagicMock()
        ev.room_id = room_id or "!room:example.org"
        ev.sender = sender
        ev.event_id = event_id or f"$evt-{sender}-{body}"
        ev.timestamp = int((ts or _t.time()) * 1000)
        ev.server_timestamp = ev.timestamp
        ev.content = {"msgtype": msgtype, "body": body}
        return ev

    @pytest.mark.asyncio
    async def test_own_sender_case_insensitive_dropped(self):
        # Simulate whoami returning a differently-cased copy of our MXID.
        self.adapter._user_id = "@Bot:Example.ORG"
        ev = self._mk_event(sender="@bot:example.org")
        await self.adapter._on_room_message(ev)
        self.adapter._handle_text_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_bridge_sender_dropped_before_pairing(self):
        ev = self._mk_event(sender="@_telegram_12345:bridge.example.org")
        await self.adapter._on_room_message(ev)
        # Bridge / appservice identities must never flow through to the
        # gateway — otherwise they trigger pairing (#15763).
        self.adapter._handle_text_message.assert_not_called()


    @pytest.mark.asyncio
    async def test_unauthorized_user_reaches_text_handler(self):
        """MATRIX_ALLOWED_USERS is enforced by gateway authz, not adapter intake."""
        self.adapter._allowed_user_ids = {"@alice:example.org"}
        ev = self._mk_event(sender="@mallory:example.org", body="hello bot")
        await self.adapter._on_room_message(ev)
        self.adapter._handle_text_message.assert_awaited_once()


    @pytest.mark.asyncio
    async def test_unauthorized_room_is_dropped(self):
        self.adapter._allowed_room_ids = {"!allowed:example.org"}
        self.adapter._is_dm_room = AsyncMock(return_value=False)
        ev = self._mk_event(
            sender="@alice:example.org",
            body="hello bot",
            room_id="!other:example.org",
        )
        await self.adapter._on_room_message(ev)
        self.adapter._handle_text_message.assert_not_called()


    @pytest.mark.asyncio
    async def test_notice_message_can_be_enabled(self):
        self.adapter._process_notices = True
        ev = self._mk_event(
            sender="@alice:example.org",
            body="human-authored notice",
            msgtype="m.notice",
        )
        await self.adapter._on_room_message(ev)
        self.adapter._handle_text_message.assert_awaited_once()


class TestMatrixRequireMention:
    """require_mention should honor config.extra like thread_require_mention."""


    @pytest.mark.asyncio
    async def test_require_mention_false_allows_unmentioned_group_message(self):
        from plugins.platforms.matrix.adapter import MatrixAdapter

        config = PlatformConfig(
            enabled=True,
            token="syt_test",
            extra={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
                "require_mention": False,
            },
        )
        adapter = MatrixAdapter(config)
        adapter._is_dm_room = AsyncMock(return_value=False)
        adapter._resolve_room_identity = AsyncMock(
            return_value=MagicMock(display_name="Project Room")
        )
        adapter._get_display_name = AsyncMock(return_value="Alice")
        adapter._background_read_receipt = MagicMock()

        ctx = await adapter._resolve_message_context(
            room_id="!project:example.org",
            sender="@alice:example.org",
            event_id="$unmentioned",
            body="hello there",
            source_content={"body": "hello there"},
            relates_to={},
        )

        assert ctx is not None


class TestMatrixFreeResponsePolicy:
    def setup_method(self):
        self.adapter = _make_adapter()
        self.adapter._user_id = "@bot:example.org"
        self.adapter._require_mention = True
        self.adapter._free_rooms = {"!free:example.org"}
        self.adapter._is_dm_room = AsyncMock(return_value=False)
        self.adapter._resolve_room_identity = AsyncMock(
            return_value=MagicMock(display_name="Free Room")
        )
        self.adapter._get_display_name = AsyncMock(return_value="Alice")
        self.adapter._background_read_receipt = MagicMock()

    @pytest.mark.asyncio
    async def test_free_response_room_allows_unmentioned_message(self):
        ctx = await self.adapter._resolve_message_context(
            room_id="!free:example.org",
            sender="@alice:example.org",
            event_id="$free",
            body="hello there",
            source_content={"body": "hello there"},
            relates_to={},
        )

        assert ctx is not None


class TestMatrixClockSkewWarning:
    """Clock-skew detector for #12614.

    Reporter's host clock was set ~2 hours ahead of real time.  The grace
    filter `event_ts < startup_ts - 5` then drops every live event because
    server timestamps look "older than startup".  When this happens well
    after startup (>30s), the adapter logs a one-shot WARNING pointing the
    user at NTP instead of failing silently.
    """

    def setup_method(self):
        self.adapter = _make_adapter()
        self.adapter._user_id = "@bot:example.org"
        self.adapter._handle_text_message = AsyncMock()
        self.adapter._handle_media_message = AsyncMock()

    @staticmethod
    def _mk_event(sender, ts_ms, event_id=None):
        ev = MagicMock()
        ev.room_id = "!room:example.org"
        ev.sender = sender
        ev.event_id = event_id or f"$evt-{sender}-{ts_ms}"
        ev.timestamp = ts_ms
        ev.server_timestamp = ts_ms
        ev.content = {"msgtype": "m.text", "body": "hi"}
        return ev

    @pytest.mark.asyncio
    async def test_late_drops_emit_one_shot_clock_skew_warning(self, caplog):
        import logging
        import time as _t

        # Simulate the reporter's environment: host clock is ~2 hours ahead
        # of server time.  Startup happened "in the future" relative to the
        # real-world events we're now receiving.
        now = _t.time()
        self.adapter._startup_ts = now - 60  # bot started 60s ago (wall clock)
        # Server events are dated 2h before startup_ts (skewed clock).
        skewed_event_ts_ms = int((self.adapter._startup_ts - 7200) * 1000)

        with caplog.at_level(logging.WARNING, logger="plugins.platforms.matrix.adapter"):
            for i in range(5):
                ev = self._mk_event(
                    sender=f"@alice{i}:example.org", ts_ms=skewed_event_ts_ms
                )
                await self.adapter._on_room_message(ev)

        # Handler should never be invoked — all events failed the grace check.
        self.adapter._handle_text_message.assert_not_called()
        # Exactly one WARNING from THIS logger should be emitted.  Filter by
        # logger name so unrelated stdlib/library warnings can't satisfy the
        # assertion.
        skew_warnings = [
            r for r in caplog.records
            if r.name == "plugins.platforms.matrix.adapter"
            and r.levelname == "WARNING"
            and "set-ntp" in r.getMessage()
        ]
        assert len(skew_warnings) == 1, (
            f"expected exactly 1 clock-skew warning, got {len(skew_warnings)}"
        )
        msg = skew_warnings[0].getMessage()
        assert "7200" in msg, f"skew value missing from message: {msg!r}"
        # Pin the counter so a regression in the gating logic (e.g. warning
        # at threshold 1 or 5, or not stopping after warn) is caught.
        assert self.adapter._late_grace_drops == 3
        assert self.adapter._clock_skew_warned is True

    @pytest.mark.asyncio
    async def test_initial_sync_drops_do_not_warn(self, caplog):
        """During the first 30s after startup, old events are normal backfill."""
        import logging
        import time as _t

        now = _t.time()
        # Startup was 1s ago — we're still in the initial-sync window.
        self.adapter._startup_ts = now - 1
        old_ts_ms = int((self.adapter._startup_ts - 3600) * 1000)

        with caplog.at_level(logging.WARNING, logger="plugins.platforms.matrix.adapter"):
            for i in range(5):
                ev = self._mk_event(
                    sender=f"@alice{i}:example.org", ts_ms=old_ts_ms
                )
                await self.adapter._on_room_message(ev)

        # Backfill drops are silent — no clock-skew warning fired.
        assert self.adapter._clock_skew_warned is False
        skew_warnings = [
            r for r in caplog.records
            if r.name == "plugins.platforms.matrix.adapter"
            and "set-ntp" in r.getMessage()
        ]
        assert skew_warnings == []


# ---------------------------------------------------------------------------
# DM auto-thread
# ---------------------------------------------------------------------------

class TestMatrixDmAutoThread:
    def setup_method(self):
        self.adapter = _make_adapter()
        self.adapter._is_dm_room = AsyncMock(return_value=True)
        self.adapter._get_display_name = AsyncMock(return_value="Alice")
        self.adapter._background_read_receipt = MagicMock()
        # Disable require_mention so DMs pass gating
        self.adapter._require_mention = False

    @pytest.mark.asyncio
    async def test_dm_auto_thread_enabled_creates_thread(self):
        """When dm_auto_thread is True, DM messages get auto-threaded."""
        self.adapter._dm_auto_thread = True

        ctx = await self.adapter._resolve_message_context(
            room_id="!dm:ex",
            sender="@alice:ex",
            event_id="$ev1",
            body="hello",
            source_content={"body": "hello"},
            relates_to={},
        )

        assert ctx is not None
        _body, _is_dm, _chat_type, thread_id, _display, _source, _is_mentioned = ctx
        assert thread_id == "$ev1"


# ---------------------------------------------------------------------------
# Proxy configuration
# ---------------------------------------------------------------------------

class TestMatrixProxyConfig:
    """Verify that MatrixAdapter resolves and propagates proxy settings."""

    def _make_adapter(self, monkeypatch, proxy_env=None):
        monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "syt_test")
        monkeypatch.setenv("MATRIX_HOMESERVER", "https://matrix.example.org")
        # Clear generic proxy vars so they don't leak from the host
        for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                    "https_proxy", "http_proxy", "all_proxy", "MATRIX_PROXY"):
            monkeypatch.delenv(key, raising=False)
        if proxy_env:
            for k, v in proxy_env.items():
                monkeypatch.setenv(k, v)
        with patch.dict("sys.modules", _make_fake_mautrix()):
            from plugins.platforms.matrix.adapter import MatrixAdapter
            cfg = PlatformConfig(enabled=True, token="syt_test",
                                 extra={"homeserver": "https://matrix.example.org",
                                        "user_id": "@bot:example.org"})
            return MatrixAdapter(cfg)


    def test_matrix_proxy_takes_priority(self, monkeypatch):
        adapter = self._make_adapter(monkeypatch,
                                     proxy_env={"MATRIX_PROXY": "socks5://special:1080",
                                                "HTTPS_PROXY": "http://generic:8080"})
        assert adapter._proxy_url == "socks5://special:1080"


class TestCreateMatrixSession:
    """Verify _create_matrix_session applies proxy at the session level."""

    @pytest.mark.asyncio
    async def test_no_proxy_returns_trust_env_session(self):
        with patch.dict("sys.modules", _make_fake_mautrix()):
            from plugins.platforms.matrix.adapter import _create_matrix_session
            session = _create_matrix_session(None)
            try:
                assert session.trust_env is True
            finally:
                await session.close()


class TestMatrixDeadInviteHandling:
    """Tests for safe pending invites and abandoned-room cleanup.

    Regression: pending-invite reconciliation used to schedule every room in
    ``rooms.invite`` after the normal invite handler had already rejected an
    unauthorized inviter. That bypassed sender authorization and joined the
    bot to private rooms it must not enter.

    Separately, when a room had no current members, ``join_room`` raised
    ``MUnknown: Can't join remote room because no servers that are in the
    room have been provided``. The pending invite stayed in the bot's view
    of the world, so every gateway restart re-attempted the join and
    re-emitted the warning indefinitely. There was no path that ever
    cleared the invite.
    """

    def setup_method(self):
        self.adapter = _make_adapter()
        self.adapter._refresh_dm_cache = AsyncMock()

    def test_unauthorized_pending_invite_is_not_scheduled(self):
        self.adapter._allowed_user_ids = {"@owner:example.org"}
        self.adapter._schedule_invite_join = MagicMock()
        sync_data = {
            "rooms": {
                "invite": {
                    "!private:example.org": {
                        "invite_state": {
                            "events": [
                                {
                                    "type": "m.room.member",
                                    "sender": "@outsider:example.org",
                                    "state_key": "@bot:example.org",
                                    "content": {
                                        "membership": "invite",
                                        "is_direct": True,
                                    },
                                }
                            ]
                        }
                    }
                }
            }
        }

        self.adapter._schedule_pending_invite_joins(sync_data)

        self.adapter._schedule_invite_join.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_servers_error_triggers_leave(self):
        join_err = Exception(
            "Can't join remote room because no servers that are in the "
            "room have been provided."
        )
        self.adapter._client = types.SimpleNamespace(
            join_room=AsyncMock(side_effect=join_err),
            leave_room=AsyncMock(),
        )

        result = await self.adapter._join_room_by_id("!dead:example.org")

        assert result is False
        self.adapter._client.leave_room.assert_awaited_once()
        # leave_room receives a RoomID-wrapped value; verify the underlying str.
        leave_arg = self.adapter._client.leave_room.await_args.args[0]
        assert str(leave_arg) == "!dead:example.org"

    @pytest.mark.asyncio
    async def test_room_not_found_error_triggers_leave(self):
        join_err = Exception("M_NOT_FOUND: Room not found")
        self.adapter._client = types.SimpleNamespace(
            join_room=AsyncMock(side_effect=join_err),
            leave_room=AsyncMock(),
        )

        await self.adapter._join_room_by_id("!gone:example.org")
        self.adapter._client.leave_room.assert_awaited_once()


# ---------------------------------------------------------------------------
# Device ID resolution when whoami returns None
# ---------------------------------------------------------------------------

class TestDeviceIdNoneResolution:
    """connect() should resolve device_id when whoami returns None."""

    @pytest.mark.asyncio
    async def test_none_device_id_resolved_via_query_keys(self):
        """query_keys({mxid: []}) with exactly one device should adopt that ID."""
        from plugins.platforms.matrix.adapter import MatrixAdapter

        config = PlatformConfig(
            enabled=True,
            token="syt_test_access_token",
            extra={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
                "encryption": True,
            },
        )
        adapter = MatrixAdapter(config)

        fake_mautrix_mods = _make_fake_mautrix()

        mock_client = MagicMock()
        mock_client.mxid = "@bot:example.org"
        mock_client.device_id = None
        mock_client.state_store = MagicMock()
        mock_client.sync_store = MagicMock()
        mock_client.crypto = None
        mock_client.whoami = AsyncMock(return_value=MagicMock(
            user_id="@bot:example.org", device_id=None,
        ))

        resolve_resp = MagicMock()
        resolve_dev = MagicMock()
        resolve_dev.keys = {"ed25519:RESOLVED_DEV": "fake_ed25519_key"}
        resolve_resp.device_keys = {"@bot:example.org": {"RESOLVED_DEV": resolve_dev}}

        verify_resp = MagicMock()
        verify_dev = MagicMock()
        verify_dev.keys = {"ed25519:RESOLVED_DEV": "fake_ed25519_key"}
        verify_resp.device_keys = {"@bot:example.org": {"RESOLVED_DEV": verify_dev}}
        mock_client.query_keys = AsyncMock(side_effect=[resolve_resp, verify_resp])

        mock_client.sync = AsyncMock(return_value={"rooms": {"join": {"!room:server": {}}}})
        mock_client.add_event_handler = MagicMock()
        mock_client.handle_sync = MagicMock(return_value=[])
        mock_client.api = MagicMock()
        mock_client.api.token = "syt_test_access_token"
        mock_client.api.session = MagicMock()
        mock_client.api.session.close = AsyncMock()

        mock_olm = MagicMock()
        mock_olm.load = AsyncMock()
        mock_olm.share_keys = AsyncMock()
        mock_olm.share_keys_min_trust = None
        mock_olm.send_keys_min_trust = None
        mock_olm.account = MagicMock()
        mock_olm.account.identity_keys = {"ed25519": "fake_ed25519_key"}

        fake_mautrix_mods["mautrix.client"].Client = MagicMock(return_value=mock_client)
        fake_mautrix_mods["mautrix.crypto"].OlmMachine = MagicMock(return_value=mock_olm)

        import plugins.platforms.matrix.adapter as matrix_mod
        with patch.object(matrix_mod, "_check_e2ee_deps", return_value=True):
            with patch.dict("sys.modules", fake_mautrix_mods):
                with patch.object(adapter, "_refresh_dm_cache", AsyncMock()):
                    with patch.object(adapter, "_sync_loop", AsyncMock(return_value=None)):
                        result = await adapter.connect()

        assert result is True
        assert adapter._device_id_unverified is False
        # Positive path (W1 hardening, salvage of #53997): the resolution query
        # must use an empty device list ({mxid: []}), and once RESOLVED_DEV is
        # adopted the verification query must carry the REAL id, never [None]
        # (the [null] body Synapse/Dendrite reject — the original bug).
        assert mock_client.device_id == "RESOLVED_DEV"
        assert mock_client.query_keys.await_count == 2
        _resolution_call, _verify_call = mock_client.query_keys.await_args_list
        assert _resolution_call.args[0] == {"@bot:example.org": []}
        assert _verify_call.args[0] == {"@bot:example.org": ["RESOLVED_DEV"]}
        assert None not in _verify_call.args[0]["@bot:example.org"]

        await adapter.disconnect()


class TestVerifyDeviceKeysGuards:
    """_verify_device_keys_on_server and _reverify_keys_after_upload guards."""

    @pytest.mark.asyncio
    async def test_verify_skips_when_device_id_unverified_flag_set(self):
        adapter = _make_adapter()
        adapter._device_id_unverified = True

        mock_client = MagicMock()
        mock_client.device_id = "SOME_DEVICE"
        mock_client.mxid = "@bot:example.org"
        mock_client.query_keys = AsyncMock()

        mock_olm = MagicMock()
        mock_olm.account = MagicMock()
        mock_olm.account.identity_keys = {"ed25519": "fake_key"}

        result = await adapter._verify_device_keys_on_server(mock_client, mock_olm)

        assert result is True
        mock_client.query_keys.assert_not_called()


# ---------------------------------------------------------------------------
# Reconnect-disconnect guard
# ---------------------------------------------------------------------------

class TestMatrixReconnectDisconnect:
    """connect() must disconnect existing client before reconnecting."""

    @pytest.mark.asyncio
    async def test_connect_calls_disconnect_when_client_already_set(self):
        """Reconnect closes the old client inside the held lifecycle lock."""
        adapter = _make_adapter()

        adapter._client = MagicMock()
        adapter._client.api = MagicMock()
        adapter._client.api.session = MagicMock()
        adapter._client.api.session.close = AsyncMock()
        adapter._client.whoami = AsyncMock()

        adapter._disconnect_unlocked = AsyncMock()

        fake_mautrix_mods = _make_fake_mautrix()

        mock_client = MagicMock()
        mock_client.mxid = "@bot:example.org"
        mock_client.device_id = None
        mock_client.state_store = MagicMock()
        mock_client.sync_store = MagicMock()
        mock_client.crypto = None
        mock_client.whoami = AsyncMock(return_value=MagicMock(
            user_id="@bot:example.org", device_id="NEW_DEV",
        ))
        mock_client.query_keys = AsyncMock()
        mock_client.sync = AsyncMock(return_value={"rooms": {"join": {"!room:server": {}}}})
        mock_client.add_event_handler = MagicMock()
        mock_client.handle_sync = MagicMock(return_value=[])
        mock_client.api = MagicMock()
        mock_client.api.token = "syt_test_access_token"
        mock_client.api.session = MagicMock()
        mock_client.api.session.close = AsyncMock()

        fake_mautrix_mods["mautrix.client"].Client = MagicMock(return_value=mock_client)

        import plugins.platforms.matrix.adapter as matrix_mod
        with patch.dict("sys.modules", fake_mautrix_mods):
            with patch.object(adapter, "_refresh_dm_cache", AsyncMock()):
                with patch.object(adapter, "_sync_loop", AsyncMock(return_value=None)):
                    await adapter.connect()

        adapter._disconnect_unlocked.assert_awaited_once()


class TestDeviceIdRecoveryOnReconnect:
    """_device_id_unverified must reset on every connect() call so a
    recovery after a failed resolution clears the stuck-true flag."""

    @pytest.mark.asyncio
    async def test_flag_clears_when_second_connect_resolves_device_id(self):
        """Same adapter, first connect fails to resolve, second succeeds. Flag
        must be False afterward and server verification must run on the second
        call."""
        from plugins.platforms.matrix.adapter import MatrixAdapter

        config = PlatformConfig(
            enabled=True,
            token="syt_test_access_token",
            extra={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
                "encryption": True,
            },
        )
        adapter = MatrixAdapter(config)

        fake_mautrix_mods = _make_fake_mautrix()

        # --- first connect: whoami returns no device_id, query_keys returns
        #     zero devices → flag set to True ---
        mock_client1 = MagicMock()
        mock_client1.mxid = "@bot:example.org"
        mock_client1.device_id = None
        mock_client1.state_store = MagicMock()
        mock_client1.sync_store = MagicMock()
        mock_client1.crypto = None
        mock_client1.whoami = AsyncMock(return_value=MagicMock(
            user_id="@bot:example.org", device_id=None,
        ))
        resolve_resp = MagicMock()
        resolve_resp.device_keys = {"@bot:example.org": {}}
        mock_client1.query_keys = AsyncMock(return_value=resolve_resp)
        mock_client1.sync = AsyncMock(return_value={"rooms": {"join": {"!room:server": {}}}})
        mock_client1.add_event_handler = MagicMock()
        mock_client1.handle_sync = MagicMock(return_value=[])
        mock_client1.api = MagicMock()
        mock_client1.api.token = "syt_test_access_token"
        mock_client1.api.session = MagicMock()
        mock_client1.api.session.close = AsyncMock()

        mock_olm1 = MagicMock()
        mock_olm1.load = AsyncMock()
        mock_olm1.share_keys = AsyncMock()
        mock_olm1.share_keys_min_trust = None
        mock_olm1.send_keys_min_trust = None
        mock_olm1.account = MagicMock()
        mock_olm1.account.identity_keys = {"ed25519": "fake_key"}

        fake_mautrix_mods["mautrix.client"].Client = MagicMock(return_value=mock_client1)
        fake_mautrix_mods["mautrix.crypto"].OlmMachine = MagicMock(return_value=mock_olm1)

        import plugins.platforms.matrix.adapter as matrix_mod
        with patch.object(matrix_mod, "_check_e2ee_deps", return_value=True):
            with patch.dict("sys.modules", fake_mautrix_mods):
                with patch.object(adapter, "_refresh_dm_cache", AsyncMock()):
                    with patch.object(adapter, "_sync_loop", AsyncMock(return_value=None)):
                        await adapter.connect()

        assert adapter._device_id_unverified is True
        await adapter.disconnect()

        # --- second connect (same adapter, re-attaching): whoami returns a
        #     real device_id this time → flag must be False ---
        mock_client2 = MagicMock()
        mock_client2.mxid = "@bot:example.org"
        mock_client2.device_id = None
        mock_client2.state_store = MagicMock()
        mock_client2.sync_store = MagicMock()
        mock_client2.crypto = None
        mock_client2.whoami = AsyncMock(return_value=MagicMock(
            user_id="@bot:example.org", device_id=None,
        ))
        resolve_resp2 = MagicMock()
        resolve_dev = MagicMock()
        resolve_dev.keys = {"ed25519:DEV2": "fake_ed25519_key2"}
        resolve_resp2.device_keys = {"@bot:example.org": {"DEV2": resolve_dev}}
        verify_resp = MagicMock()
        verify_dev = MagicMock()
        verify_dev.keys = {"ed25519:DEV2": "fake_ed25519_key2"}
        verify_resp.device_keys = {"@bot:example.org": {"DEV2": verify_dev}}
        mock_client2.query_keys = AsyncMock(side_effect=[resolve_resp2, verify_resp])
        mock_client2.sync = AsyncMock(return_value={"rooms": {"join": {"!room:server": {}}}})
        mock_client2.add_event_handler = MagicMock()
        mock_client2.handle_sync = MagicMock(return_value=[])
        mock_client2.api = MagicMock()
        mock_client2.api.token = "syt_test_access_token"
        mock_client2.api.session = MagicMock()
        mock_client2.api.session.close = AsyncMock()

        mock_olm2 = MagicMock()
        mock_olm2.load = AsyncMock()
        mock_olm2.share_keys = AsyncMock()
        mock_olm2.share_keys_min_trust = None
        mock_olm2.send_keys_min_trust = None
        mock_olm2.account = MagicMock()
        mock_olm2.account.identity_keys = {"ed25519": "fake_ed25519_key2"}

        fake_mautrix_mods["mautrix.client"].Client = MagicMock(return_value=mock_client2)
        fake_mautrix_mods["mautrix.crypto"].OlmMachine = MagicMock(return_value=mock_olm2)

        with patch.object(matrix_mod, "_check_e2ee_deps", return_value=True):
            with patch.dict("sys.modules", fake_mautrix_mods):
                with patch.object(adapter, "_refresh_dm_cache", AsyncMock()):
                    with patch.object(adapter, "_sync_loop", AsyncMock(return_value=None)):
                        result = await adapter.connect()

        assert result is True
        assert adapter._device_id_unverified is False
        # Verification must genuinely re-run on the second connect — not just
        # the resolution query. Two awaited query_keys calls: resolution
        # ({mxid: []}) then verification ({mxid: [<resolved id>]}). The
        # verification call must carry the REAL resolved device id ("DEV2"),
        # never [None] (the original bug). (W2 hardening, salvage of #53997)
        assert mock_client2.query_keys.await_count == 2
        _resolution_call, _verify_call = mock_client2.query_keys.await_args_list
        assert _resolution_call.args[0] == {"@bot:example.org": []}
        assert _verify_call.args[0] == {"@bot:example.org": ["DEV2"]}
        assert None not in _verify_call.args[0]["@bot:example.org"]

        await adapter.disconnect()


class TestMatrixDispatchSyncIsolation:
    """A failing mautrix event handler must not abort the whole sync batch.

    ``_dispatch_sync`` gathers the per-event handler tasks. Without
    ``return_exceptions=True`` the first exception aborts the gather and the
    sibling events in the same sync response are silently dropped.
    """

    @pytest.mark.asyncio
    async def test_dispatch_sync_isolates_failing_handler(self, caplog):
        import logging

        adapter = _make_adapter()
        ran = {"ok": False}

        async def _boom():
            raise RuntimeError("handler boom")

        async def _ok():
            ran["ok"] = True

        client = MagicMock()
        client.handle_sync = MagicMock(return_value=[_boom(), _ok()])
        adapter._client = client

        with caplog.at_level(logging.WARNING):
            # Must not raise despite the failing handler.
            await adapter._dispatch_sync({"next_batch": "s1"})

        assert ran["ok"] is True  # the sibling handler still ran
        assert "event handler failed" in caplog.text  # failure surfaced, not swallowed


# ---------------------------------------------------------------------------
# E2EE crypto store/device mismatch must fail closed
# ---------------------------------------------------------------------------

class TestCryptoStoreDeviceMismatch:
    @pytest.mark.asyncio
    async def test_mismatch_refuses_without_store_reset(self, caplog):
        import logging
        adapter = _make_adapter()
        store = MagicMock()
        store.get_device_id = AsyncMock(return_value="OLDDEVICE")
        store.delete = AsyncMock()

        with caplog.at_level(logging.ERROR), pytest.raises(
            RuntimeError, match="crypto-store device mismatch"
        ):
            await adapter._reset_crypto_store_if_device_changed(store, "NEWDEVICE")

        store.delete.assert_not_awaited()
        assert "without changing the store" in caplog.text

    @pytest.mark.asyncio
    async def test_server_identity_mismatch_never_deletes_or_reuploads(self, caplog):
        import logging
        adapter = _make_adapter()
        client = MagicMock()
        client.mxid = "@bot:example.org"
        client.device_id = "DEVICE"
        client.query_keys = AsyncMock(
            return_value=MagicMock(
                device_keys={
                    "@bot:example.org": {
                        "DEVICE": MagicMock(keys={"ed25519:DEVICE": "server-key"})
                    }
                }
            )
        )
        client.api = MagicMock()
        client.api.request = AsyncMock()
        olm = MagicMock()
        olm.account.identity_keys = {"ed25519": "local-key"}
        olm.share_keys = AsyncMock()

        with caplog.at_level(logging.ERROR):
            assert await adapter._verify_device_keys_on_server(client, olm) is False

        client.api.request.assert_not_awaited()
        olm.share_keys.assert_not_awaited()
        assert "without deleting the device" in caplog.text

    @pytest.mark.asyncio
    async def test_no_reset_when_device_id_same(self):
        adapter = _make_adapter()
        store = MagicMock()
        store.get_device_id = AsyncMock(return_value="SAMEDEVICE")
        store.delete = AsyncMock()

        assert await adapter._reset_crypto_store_if_device_changed(store, "SAMEDEVICE") is False
        store.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_reset_on_fresh_store(self):
        adapter = _make_adapter()
        store = MagicMock()
        store.get_device_id = AsyncMock(return_value=None)
        store.delete = AsyncMock()

        assert await adapter._reset_crypto_store_if_device_changed(store, "NEWDEVICE") is False
        store.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_reset_without_device_id(self):
        adapter = _make_adapter()
        store = MagicMock()
        store.get_device_id = AsyncMock(return_value="OLDDEVICE")
        store.delete = AsyncMock()

        assert await adapter._reset_crypto_store_if_device_changed(store, "") is False
        store.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_connect_refuses_when_token_device_differs_from_store(
        self, caplog
    ):
        """A rotated token plus stale store must not trigger an automatic reset."""
        import logging
        from plugins.platforms.matrix.adapter import MatrixAdapter

        config = PlatformConfig(
            enabled=True,
            token="syt_rotated_access_token",
            extra={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
                "encryption": True,
                "device_id": "DEVICE_A",
            },
        )
        adapter = MatrixAdapter(config)

        fake_mautrix_mods = _make_fake_mautrix()

        deleted = {"count": 0}

        class _ResettableCryptoStore:
            upgrade_table = MagicMock()

            def __init__(self, account_id="", pickle_key="", db=None):
                self.account_id = account_id
                self.pickle_key = pickle_key
                self.db = db
                self._device_id = "DEVICE_A"  # persisted from the old token

            async def open(self):
                pass

            async def get_device_id(self):
                return self._device_id

            async def delete(self):
                deleted["count"] += 1
                self._device_id = ""

            async def put_device_id(self, device_id):
                self._device_id = device_id

        fake_mautrix_mods[
            "mautrix.crypto.store.asyncpg"
        ].PgCryptoStore = _ResettableCryptoStore

        mock_client = MagicMock()
        mock_client.mxid = "@bot:example.org"
        mock_client.device_id = None
        mock_client.state_store = MagicMock()
        mock_client.sync_store = MagicMock()
        mock_client.crypto = None
        # Token was rotated: the homeserver reports device B.
        mock_client.whoami = AsyncMock(
            return_value=MagicMock(user_id="@bot:example.org", device_id="DEVICE_B")
        )
        mock_client.sync = AsyncMock(return_value={"rooms": {"join": {}}})
        mock_client.add_event_handler = MagicMock()
        mock_client.handle_sync = MagicMock(return_value=[])
        mock_client.query_keys = AsyncMock(return_value={"device_keys": {}})
        mock_client.api = MagicMock()
        mock_client.api.token = "syt_rotated_access_token"
        mock_client.api.session = MagicMock()
        mock_client.api.session.close = AsyncMock()

        mock_olm = MagicMock()
        mock_olm.load = AsyncMock()
        mock_olm.share_keys = AsyncMock()
        mock_olm.share_keys_min_trust = None
        mock_olm.send_keys_min_trust = None
        mock_olm.account = MagicMock()
        mock_olm.account.identity_keys = {"ed25519": "fake_ed25519_key"}

        fake_mautrix_mods["mautrix.client"].Client = MagicMock(
            return_value=mock_client
        )
        fake_mautrix_mods["mautrix.crypto"].OlmMachine = MagicMock(
            return_value=mock_olm
        )

        import plugins.platforms.matrix.adapter as matrix_mod

        with caplog.at_level(logging.ERROR), patch.object(
            matrix_mod, "_check_e2ee_deps", return_value=True
        ), patch.dict("sys.modules", fake_mautrix_mods), patch.object(
            adapter, "_refresh_dm_cache", AsyncMock()
        ), patch.object(
            adapter, "_sync_loop", AsyncMock(return_value=None)
        ), patch.object(
            adapter, "_verify_device_keys_on_server", AsyncMock(return_value=True)
        ):
            assert await adapter.connect() is False

        assert mock_client.device_id == "DEVICE_B"
        assert deleted["count"] == 0
        assert "without changing the store" in caplog.text


# ---------------------------------------------------------------------------
# Crypto store pickle-key migration
# ---------------------------------------------------------------------------

class TestCryptoPickleKeyMigration:
    @pytest.mark.asyncio
    async def test_account_loads_fine_no_migration(self):
        adapter = _make_adapter()
        store = MagicMock()
        store.get_account = AsyncMock(return_value=MagicMock())
        assert await adapter._migrate_legacy_crypto_pickle(
            store, MagicMock(), "@bot:example.org", "@bot:example.org:DEV"
        ) is True
        store.put_account.assert_not_called()

    @pytest.mark.asyncio
    async def test_migrates_from_default_pickle_key(self, caplog):
        import logging
        adapter = _make_adapter()
        store = MagicMock()
        store.get_account = AsyncMock(side_effect=RuntimeError("BAD_ACCOUNT_KEY"))
        store.put_account = AsyncMock()

        legacy_account = MagicMock()
        created = []

        class FakePgCryptoStore:
            def __init__(self, account_id, pickle_key, db):
                self.pickle_key = pickle_key
                created.append(pickle_key)

            async def get_account(self):
                if self.pickle_key == "@bot:example.org:default":
                    return legacy_account
                raise RuntimeError("BAD_ACCOUNT_KEY")

        crypto_db = MagicMock()
        crypto_db.fetch = AsyncMock(return_value=[])
        crypto_db.execute = AsyncMock()

        fake_mod = types.ModuleType("mautrix.crypto.store.asyncpg")
        fake_mod.PgCryptoStore = FakePgCryptoStore
        with patch.dict(
            sys.modules,
            {
                "mautrix.crypto.store.asyncpg": fake_mod,
                # _repickle_crypto_sessions imports the olm C-extension;
                # fake it so this test does not require libolm.
                "olm": self._fake_olm_module(),
            },
        ), caplog.at_level(logging.INFO):
            result = await adapter._migrate_legacy_crypto_pickle(
                store, crypto_db, "@bot:example.org", "@bot:example.org:NEWDEV"
            )

        assert result is True
        store.put_account.assert_awaited_once_with(legacy_account)
        assert "re-pickled crypto store account" in caplog.text
        assert "@bot:example.org:default" in created
        # session re-pickle pass must sweep all three session tables
        queried = " ".join(str(c.args[0]) for c in crypto_db.fetch.await_args_list)
        for table in (
            "crypto_olm_session",
            "crypto_megolm_inbound_session",
            "crypto_megolm_outbound_session",
        ):
            assert table in queried

    @pytest.mark.asyncio
    async def test_unrecoverable_pickle_logs_error(self, caplog):
        import logging
        adapter = _make_adapter()
        store = MagicMock()
        store.get_account = AsyncMock(side_effect=RuntimeError("BAD_ACCOUNT_KEY"))
        store.put_account = AsyncMock()

        class FakePgCryptoStore:
            def __init__(self, account_id, pickle_key, db):
                pass

            async def get_account(self):
                raise RuntimeError("BAD_ACCOUNT_KEY")

        fake_mod = types.ModuleType("mautrix.crypto.store.asyncpg")
        fake_mod.PgCryptoStore = FakePgCryptoStore
        with patch.dict(sys.modules, {"mautrix.crypto.store.asyncpg": fake_mod}), \
                caplog.at_level(logging.ERROR):
            result = await adapter._migrate_legacy_crypto_pickle(
                store, MagicMock(), "@bot:example.org", "@bot:example.org:NEWDEV"
            )

        assert result is False
        store.put_account.assert_not_awaited()
        assert "cannot be unpickled" in caplog.text

    def _fake_olm_module(self):
        """Fake the `olm` C-extension module.

        _repickle_crypto_sessions does `import olm`, which needs libolm.
        Sessions unpickle only with the key they were pickled under.
        """
        olm_mod = types.ModuleType("olm")

        class _Session:
            def __init__(self, key):
                self._key = key

            @classmethod
            def from_pickle(cls, blob, key):
                pickled_under = blob.decode().split("|")[1]
                if pickled_under != key:
                    raise RuntimeError("BAD_ACCOUNT_KEY")
                return cls(key)

            def pickle(self, key):
                return f"sess|{key}".encode()

        for name in ("Session", "InboundGroupSession", "OutboundGroupSession"):
            setattr(olm_mod, name, type(name, (_Session,), {}))
        return olm_mod

    @pytest.mark.asyncio
    async def test_session_rows_are_repickled_under_current_key(self):
        """The session sweep must actually rewrite legacy-key rows."""
        adapter = _make_adapter()
        legacy = "@bot:example.org:default"
        current = "@bot:example.org:NEWDEV"

        crypto_db = MagicMock()
        crypto_db.fetch = AsyncMock(
            return_value=[{"session_id": "s1", "session": f"sess|{legacy}".encode()}]
        )
        crypto_db.execute = AsyncMock()

        with patch.dict(sys.modules, {"olm": self._fake_olm_module()}):
            await adapter._repickle_crypto_sessions(
                crypto_db, "@bot:example.org", legacy, current
            )

        # One UPDATE per session table, each writing the current-key blob.
        assert crypto_db.execute.await_count == 3
        for call in crypto_db.execute.await_args_list:
            assert call.args[1] == f"sess|{current}".encode()
            assert call.args[3] == "s1"

    @pytest.mark.asyncio
    async def test_rows_already_on_current_key_are_left_alone(self):
        adapter = _make_adapter()
        current = "@bot:example.org:NEWDEV"

        crypto_db = MagicMock()
        crypto_db.fetch = AsyncMock(
            return_value=[{"session_id": "s1", "session": f"sess|{current}".encode()}]
        )
        crypto_db.execute = AsyncMock()

        with patch.dict(sys.modules, {"olm": self._fake_olm_module()}):
            await adapter._repickle_crypto_sessions(
                crypto_db, "@bot:example.org", "@bot:example.org:default", current
            )

        crypto_db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unreadable_rows_are_left_in_place_not_dropped(self, caplog):
        """A row readable under neither key is skipped and left untouched.

        The log must not claim the row was dropped when no DELETE is issued.
        """
        import logging
        adapter = _make_adapter()

        crypto_db = MagicMock()
        crypto_db.fetch = AsyncMock(
            return_value=[{"session_id": "s1", "session": b"sess|@bot:other:KEY"}]
        )
        crypto_db.execute = AsyncMock()

        with patch.dict(sys.modules, {"olm": self._fake_olm_module()}), \
                caplog.at_level(logging.WARNING):
            await adapter._repickle_crypto_sessions(
                crypto_db,
                "@bot:example.org",
                "@bot:example.org:default",
                "@bot:example.org:NEWDEV",
            )

        crypto_db.execute.assert_not_awaited()
        assert "leaving it in place" in caplog.text
        assert "dropping" not in caplog.text.lower()

    @pytest.mark.asyncio
    async def test_failed_sweep_leaves_account_on_legacy_key_and_retries(
        self, caplog
    ):
        """A sweep failure must not commit the account.

        The account is the migration's commit marker: if it is written first
        and the sweep then fails, the next startup takes the current-key fast
        path and the remaining legacy-key sessions are stranded permanently.
        """
        import logging
        adapter = _make_adapter()
        legacy_account = MagicMock()

        store = MagicMock()
        store.get_account = AsyncMock(side_effect=RuntimeError("BAD_ACCOUNT_KEY"))
        store.put_account = AsyncMock()

        class FakePgCryptoStore:
            def __init__(self, account_id, pickle_key, db):
                self.pickle_key = pickle_key

            async def get_account(self):
                if self.pickle_key == "@bot:example.org:default":
                    return legacy_account
                raise RuntimeError("BAD_ACCOUNT_KEY")

        crypto_db = MagicMock()
        crypto_db.fetch = AsyncMock(side_effect=RuntimeError("db went away"))
        crypto_db.execute = AsyncMock()

        fake_mod = types.ModuleType("mautrix.crypto.store.asyncpg")
        fake_mod.PgCryptoStore = FakePgCryptoStore

        with patch.dict(
            sys.modules,
            {
                "mautrix.crypto.store.asyncpg": fake_mod,
                "olm": self._fake_olm_module(),
            },
        ), caplog.at_level(logging.ERROR):
            result = await adapter._migrate_legacy_crypto_pickle(
                store, crypto_db, "@bot:example.org", "@bot:example.org:NEWDEV"
            )

        assert result is False
        # The critical assertion: the account was NOT committed, so the next
        # start still sees a legacy-key account and retries the migration.
        store.put_account.assert_not_awaited()
        assert "retried on the next start" in caplog.text
