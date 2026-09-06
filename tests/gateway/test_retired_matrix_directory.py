import asyncio
import json
from gateway import channel_directory as cd


def test_retired_matrix_targets_filtered_on_read_and_rebuild(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(cd, "DIRECTORY_PATH", tmp_path / "channel_directory.json")
    monkeypatch.setattr(cd, "CHANNEL_ALIASES_PATH", tmp_path / "channel_aliases.json")
    old = "!old:nope.chat"
    current = "!new:example.org"
    modern = "!opaque-room-v12"
    external = "!room:example.org"
    entries = [{"id": x, "name": x, "type": "group", "thread_id": None}
               for x in [old, old + ":$thread", current, modern, external]]
    cd.DIRECTORY_PATH.write_text(json.dumps({"platforms": {"matrix": entries, "email": entries}}))
    cd.CHANNEL_ALIASES_PATH.write_text(json.dumps({"matrix": {old: "Retired alias"}}))
    result = cd.load_directory()
    assert [x["id"] for x in result["platforms"]["matrix"]] == [current, modern, external]
    assert result["platforms"]["email"] == entries
    assert cd.resolve_channel_name("matrix", "Retired alias") is None
    class Adapter:
        async def list_channels(self):
            return entries
    # Matrix may be a dynamically registered plugin; a value-bearing key is sufficient.
    class MatrixKey:
        value = "matrix"
    rebuilt = asyncio.run(cd.build_channel_directory({MatrixKey(): Adapter()}))
    assert [x["id"] for x in rebuilt["platforms"]["matrix"]] == [current, modern, external]
    assert cd.load_directory()["platforms"]["matrix"] == rebuilt["platforms"]["matrix"]
