"""Gateway-owned Matrix outbox for profile-local automation.

Workers enqueue JSON envelopes only.  The long-lived gateway Matrix adapter,
which owns the active E2EE device and crypto store, is the sole sender.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

def profile_outbox_root() -> Path:
    """Return the queue owned by this process's effective Hermes profile."""
    hermes_home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return hermes_home / "data/matrix-gateway-outbox-v2"


def _outbox_paths() -> tuple[Path, Path, Path, Path]:
    root = profile_outbox_root()
    return root / "pending", root / "sent", root / "failed", root / "reactions"


def validate_owner_contract(envelope: dict[str, Any]) -> None:
    """Fail closed unless the envelope belongs to this gateway identity."""
    effective_profile = Path(os.environ.get("HERMES_HOME") or "").name
    effective_user = os.environ.get("MATRIX_USER_ID")
    owner_profile = envelope.get("owner_profile")
    expected_user = envelope.get("expected_matrix_user_id")
    if not owner_profile or not expected_user:
        raise RuntimeError("outbox owner contract missing")
    if owner_profile != effective_profile or expected_user != effective_user:
        raise RuntimeError(
            "outbox owner mismatch: "
            f"expected profile={owner_profile} user={expected_user}; "
            f"effective profile={effective_profile} user={effective_user}"
        )


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush(); os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def enqueue(envelope: dict[str, Any]) -> Path:
    kind = envelope.get("kind")
    required = ({"id", "room_id", "event_ids"} if kind == "matrix_redaction"
                else {"id", "room_id", "body", "state_path", "message_id"})
    if kind == "linkedin_outreach":
        required |= {
            "followup_state_path", "decision_text",
            "decision_text_hash_sha256", "action_payload_hash_sha256",
        }
    required |= {"owner_profile", "expected_matrix_user_id"}
    missing = required - set(envelope)
    if missing: raise ValueError(f"outbox envelope missing: {sorted(missing)}")
    pending, _, _, _ = _outbox_paths()
    target = pending / f"{envelope['id']}.json"
    _atomic_json(target, envelope)
    return target


def record_reaction(*, room_id: str, event_id: str, sender: str, target_event_id: str, key: str) -> Path:
    """Persist one decrypted reaction received by the gateway for local workers."""
    safe_id = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in event_id)[:180]
    _, _, _, reactions = _outbox_paths()
    target = reactions / f"{safe_id}.json"
    _atomic_json(target, {
        "type": "m.reaction", "room_id": room_id, "event_id": event_id,
        "sender": sender, "origin_server_ts": int(time.time() * 1000),
        "content": {"m.relates_to": {"rel_type": "m.annotation", "event_id": target_event_id, "key": key}},
    })
    return target


def _load_delivery_state(envelope: dict[str, Any]) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    state_path = Path(str(envelope["state_path"]))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    current = state.get("current") or {}
    kind = envelope.get("kind", "linkedin_inbox")
    if kind == "linkedin_outreach":
        if current.get("editorial_job_id") != envelope["message_id"] or current.get("status") != "ready_to_publish":
            raise RuntimeError("outreach state changed before outbox delivery")
        decision_text = str(envelope.get("decision_text") or "")
        expected_text = str(
            current.get("comment_draft")
            if current.get("action_type") == "comment"
            else current.get("connection_note") or ""
        )
        expected_hash = str(
            current.get("comment_hash_sha256")
            if current.get("action_type") == "comment"
            else current.get("connection_note_hash_sha256") or ""
        )
        if (
            not expected_text
            or decision_text != expected_text
            or hashlib.sha256(decision_text.encode("utf-8")).hexdigest() != expected_hash
            or envelope.get("decision_text_hash_sha256") != expected_hash
            or envelope.get("action_payload_hash_sha256") != current.get("action_payload_hash_sha256")
            or decision_text not in str(envelope.get("body") or "")
        ):
            raise RuntimeError("outreach decision text does not match canonical state")
    elif current.get("message_id") != envelope["message_id"] or current.get("status") != "ready_to_publish":
        raise RuntimeError("state changed before outbox delivery")
    return state_path, state, current


def _apply_delivery_receipt(
    envelope: dict[str, Any], state_path: Path, state: dict[str, Any], current: dict[str, Any],
    proposal_event_id: str, seeds: dict[str, str], image_event_id: str | None,
) -> None:
    now = __import__("datetime").datetime.now().astimezone()
    current.update({
        "status": "awaiting_reaction", "proposal_event_id": proposal_event_id,
        "reaction_seed_event_ids": seeds, "proposal_sent_at": now.isoformat(timespec="seconds"),
    })
    if envelope.get("kind") == "linkedin_outreach":
        from datetime import timedelta
        current["proposal_expires_at"] = (now + timedelta(hours=24)).isoformat(timespec="seconds")
        current["proposal_image_event_id"] = image_event_id
        followup_path = Path(str(envelope["followup_state_path"]))
        followup = json.loads(followup_path.read_text(encoding="utf-8"))
        editorial = followup.get("current_editorial") or {}
        if editorial.get("job_id") != envelope["message_id"] or editorial.get("status") != "proposal_queued":
            raise RuntimeError("outreach editorial state changed before receipt")
        # Keep both canonical stores bound to the exact same Matrix choice
        # surface.  A proposal id alone is insufficient for revised outreach:
        # stale image/seed ids would otherwise remain valid in followup-state.
        editorial.update({
            "status": "proposal_published",
            "proposal_event_id": proposal_event_id,
            "proposal_image_event_id": image_event_id,
            "reaction_seed_event_ids": dict(seeds),
            "proposal_sent_at": current["proposal_sent_at"],
            "proposal_expires_at": current["proposal_expires_at"],
        })
        _atomic_json(followup_path, followup)
    _atomic_json(state_path, state)


async def consume_once(adapter: Any) -> int:
    """Deliver pending envelopes through the already-connected gateway adapter."""
    delivered = 0
    pending, sent, failed, _ = _outbox_paths()
    pending.mkdir(parents=True, exist_ok=True)
    for path in sorted(pending.glob("*.json")):
        claim = path.with_suffix(".processing")
        try:
            os.replace(path, claim)
            envelope = json.loads(claim.read_text(encoding="utf-8"))
            validate_owner_contract(envelope)
            if envelope.get("kind") == "matrix_redaction":
                for event_id in envelope.get("event_ids") or []:
                    if not await adapter.redact_message(str(envelope["room_id"]), str(event_id), str(envelope.get("reason") or "decision is no longer active")):
                        raise RuntimeError(f"gateway Matrix redaction failed: {event_id}")
                _atomic_json(sent / claim.name.replace(".processing", ".json"), envelope)
                claim.unlink(missing_ok=True); delivered += 1
                continue
            state_path, state, current = _load_delivery_state(envelope)
            for old_event_id in envelope.get("prior_reaction_event_ids") or []:
                await adapter._redact_reaction(str(envelope["room_id"]), str(old_event_id), "superseded decision")
            old_image = envelope.get("prior_image_event_id")
            if old_image:
                await adapter.redact_message(str(envelope["room_id"]), str(old_image), "superseded evidence")
            image_event_id = None
            media_path = envelope.get("media_path")
            if media_path:
                image = await adapter.send_image_file(str(envelope["room_id"]), Path(str(media_path)), caption="Beitragsbild zum folgenden Vorschlag")
                if not getattr(image, "success", False) or not getattr(image, "message_id", None):
                    raise RuntimeError(str(getattr(image, "error", "gateway Matrix image send failed")))
                image_event_id = str(image.message_id)
            result = await adapter.send(str(envelope["room_id"]), str(envelope["body"]))
            if not getattr(result, "success", False) or not getattr(result, "message_id", None):
                raise RuntimeError(str(getattr(result, "error", "gateway Matrix send failed")))
            seeds: dict[str, str] = {}
            for emoji in envelope.get("reactions") or []:
                event_id = await adapter._send_reaction(str(envelope["room_id"]), str(result.message_id), str(emoji))
                if not event_id: raise RuntimeError(f"reaction seed failed: {emoji}")
                seeds[str(emoji)] = str(event_id)
            _apply_delivery_receipt(envelope, state_path, state, current, str(result.message_id), seeds, image_event_id)
            _atomic_json(sent / claim.name.replace(".processing", ".json"), {
                **envelope,
                "proposal_event_id": str(result.message_id),
                "proposal_image_event_id": image_event_id,
                "reaction_seed_event_ids": seeds,
            })
            claim.unlink(missing_ok=True); delivered += 1
        except Exception as exc:
            logger.exception("Matrix outbox delivery failed for %s", path.name)
            try:
                data = json.loads(claim.read_text(encoding="utf-8")) if claim.exists() else {"id":path.stem}
                _atomic_json(failed / f"{data['id']}.json", {**data, "error":str(exc)[:500]})
                claim.unlink(missing_ok=True)
            except Exception: logger.exception("Matrix outbox failure recording failed")
    return delivered


async def consume_forever(adapter: Any, stop: Any, interval: float = 2.0) -> None:
    while not stop.is_set():
        try: await consume_once(adapter)
        except Exception: logger.exception("Matrix outbox pass failed")
        try: await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError: pass
