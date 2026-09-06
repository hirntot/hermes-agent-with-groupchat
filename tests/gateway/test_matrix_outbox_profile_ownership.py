from __future__ import annotations

import json
from pathlib import Path

import pytest

from gateway import matrix_outbox


def test_owner_contract_rejects_foreign_envelope(monkeypatch, tmp_path: Path) -> None:
    marlene_home = tmp_path / "profiles/marlene_hofstetter"
    monkeypatch.setenv("HERMES_HOME", str(marlene_home))
    monkeypatch.setenv("MATRIX_USER_ID", "@profile_a:example.org")
    envelope = {
        "owner_profile": "lena_brandner",
        "expected_matrix_user_id": "@profile_b:example.org",
    }

    with pytest.raises(RuntimeError, match="outbox owner mismatch"):
        matrix_outbox.validate_owner_contract(envelope)


@pytest.mark.asyncio
async def test_consumer_only_scans_effective_profile_outbox(monkeypatch, tmp_path: Path) -> None:
    profiles = tmp_path / "profiles"
    lena_home = profiles / "lena_brandner"
    marlene_home = profiles / "marlene_hofstetter"
    lena_pending = lena_home / "data/matrix-gateway-outbox/pending"
    lena_pending.mkdir(parents=True)
    envelope = {
        "id": "lena-job",
        "kind": "linkedin_outreach",
        "owner_profile": "lena_brandner",
        "expected_matrix_user_id": "@profile_b:example.org",
        "room_id": "!room:example.org",
        "body": "proposal",
        "state_path": str(lena_home / "state.json"),
        "message_id": "job-1",
    }
    (lena_pending / "lena-job.json").write_text(json.dumps(envelope), encoding="utf-8")

    monkeypatch.setenv("HERMES_HOME", str(marlene_home))
    monkeypatch.setenv("MATRIX_USER_ID", "@profile_a:example.org")

    class Adapter:
        async def send(self, *_args, **_kwargs):
            raise AssertionError("foreign consumer must not send Lena's envelope")

    assert matrix_outbox.profile_outbox_root() == (
        marlene_home / "data/matrix-gateway-outbox-v2"
    )

    delivered = await matrix_outbox.consume_once(Adapter())

    assert delivered == 0
    assert (lena_pending / "lena-job.json").exists()
    assert not any((marlene_home / "data/matrix-gateway-outbox-v2/pending").glob("*.json"))
