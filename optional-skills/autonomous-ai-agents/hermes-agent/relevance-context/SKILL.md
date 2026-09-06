---
name: relevance-context
description: "Manage per-room Matrix relevance context by editing the XML directly."
version: 0.1.4
author: Moritz (hirntot), Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [matrix, relevance, context, room]
    related_skills: [hermes-agent-skill-authoring]
---

# Relevance Context Skill

Controls how this agent behaves in Matrix group rooms. The active configuration lives in `~/.hermes/profiles/<active_profile>/RELEVANCE_CONTEXT.xml`. The `intelligent_reaction.py` filter reads this file and automatically reloads it when it changes, so editing it takes effect immediately without a gateway restart.

## When to Use

- A user asks how you should behave in a room.
- A user says you should react to name mentions, @-mentions, or specific keywords.
- A user asks you to change `answer_priority`, `relevance_factor`, the room `relation`, or the `<names>` block.
- You notice your behavior in a room is wrong and want to correct it.
- You notice a pattern of being repeatedly pinged to react, or repeatedly told to back off — see "Self-Monitoring Signals" below.

Don't use for:

- Private/direct chats do not need manual XML maintenance. On the first event, the Matrix filter deterministically creates the agent's own room entry with `answer_priority=ALWAYS`, then uses the model only to enrich relation and names; the model may not downgrade `ALWAYS`.
- One-off replies that don't need a persistent group-room behavior change.

## Prerequisites

- `MATRIX_INTELLIGENT_REACTION=true` in the active profile.
- `RELEVANCE_CONTEXT.xml` may already exist; on the first event in any unknown room, the gate synchronously creates this agent's room entry (`ALWAYS` for DMs, `ASK_AI` for groups) and then enriches it asynchronously.
- The active profile path is `~/.hermes/profiles/<active_profile>`.

## How to Run

When a room-behavior change is needed:

1. Read `~/.hermes/profiles/<active_profile>/RELEVANCE_CONTEXT.xml`.
2. Find the `<room id="...">` or `<room alias="...">` entry matching the current room.
3. Edit the fields you want to change:
   - `<answer_priority>`: `ASK_AI` (default), `WHEN_MENTIONED`, `WHEN_MENTIONED_ONLY`, or `ALWAYS` — see "Choosing a Mode" below. Only pick `ALWAYS` or `WHEN_MENTIONED_ONLY` if the user explicitly asked for exactly that extreme; otherwise stay with/return to `ASK_AI` or `WHEN_MENTIONED`.
   - `<relevance_factor>`: integer `1` to `99`
   - `<relation>`: concise German description of who you are and how to behave in this room
   - `<names>`: list common name variants the user might type, including lowercase
4. Write the XML back to the same path.
5. Confirm the change briefly to the user.

Do not invoke `update_relevance_context.py` yourself — that script is reserved for manual admin use and for the gate's first-time room creation.

## Choosing a Mode

**`ASK_AI` is the default and the right choice for almost every room.** It already means you "read along" with every message as context — a small model just decides, per message, whether it is worth an actual reply, and delays or drops the ones that are not. "Reading everything" is not the same as "replying to everything": `ASK_AI` gives you full context awareness *and* restraint at the same time. When a user asks you to "read along" or "be more dynamic" or "don't always jump in first", `ASK_AI` (with a tuned `relevance_factor`) is normally what they mean — do not jump straight to `ALWAYS`.

`ALWAYS` and `WHEN_MENTIONED_ONLY` are the two extremes and should only be set when the user **explicitly and unambiguously** asks for that exact extreme behavior (e.g. "reply to literally everything in this room" or "only ever react to a direct mention, nothing else, ever"). If a request is vague ("read everything", "be present", "don't react to everything"), prefer `ASK_AI` or `WHEN_MENTIONED` and ask a clarifying question if truly unsure — do not guess towards the extremes.

| Mode | When it fits | What actually happens |
| --- | --- | --- |
| `ASK_AI` (default) | Normal group room behavior; "read along but only reply when it makes sense". | Every message is scored 1-5 by a small model using `relevance_factor` as a bias. Low scores are delayed or dropped, high scores (or "5") flush immediately. You always have full context, but you don't reply to everything. |
| `WHEN_MENTIONED` | User wants you to keep full context of the room, but only ever reply when addressed — without an AI relevance judgment call. | All messages are buffered. Non-mention messages are delivered collectively every ~5 minutes as a read-only "for your information" note (no reply expected/wanted). A direct `@mxid`/`<names>` mention or a reply to your own thread flushes immediately and *does* expect a normal reply. |
| `WHEN_MENTIONED_ONLY` | User explicitly wants you to ignore everything except being addressed directly — no ambient context at all. | Every non-mention message is silently dropped, never delivered, not even as info. Only `@mxid`/`<names>` mentions or replies to your own thread reach you. |
| `ALWAYS` | User explicitly wants you to be a constant, first-response participant in this room. | Every normal message is delivered immediately and expects a reply, unless it is clearly addressed to someone else. Use sparingly — this makes you the default responder for the whole room. |

## Self-Monitoring Signals

There is no script that auto-tunes `relevance_factor` for you — watch for these patterns yourself and adjust proactively instead of treating the friction as normal:

- **You keep getting explicitly called by name/mention to get a reaction** ("Bastian, sag doch mal was dazu", repeated direct pings for things you should plausibly have picked up on your own): this usually means `relevance_factor` is too low (or `relation`/`<names>` is incomplete) for that room. Consider raising `relevance_factor`, or switching `WHEN_MENTIONED_ONLY` → `WHEN_MENTIONED`/`ASK_AI` if that fits the user's actual intent better.
- **You keep getting told to back off, stay quiet, or "zurückgepfiffen" for jumping in unasked**: this usually means `relevance_factor` is too high (or you're on `ALWAYS` when the room doesn't need that). Consider lowering `relevance_factor`, or moving away from `ALWAYS` towards `ASK_AI`/`WHEN_MENTIONED`.
- Treat 2-3 corrections of the same kind in a room as a signal, not noise — proactively propose (or make) a `RELEVANCE_CONTEXT.xml` adjustment for that room instead of waiting to be told explicitly every time.
- After adjusting, briefly tell the user what you changed and why, so they can veto it.

## Quick Reference

| Field | Allowed values | Meaning |
| --- | --- | --- |
| `answer_priority` | `ASK_AI` (default) | Let a small model score relevance 1-5 and delay/drop accordingly. Full context, selective replies. |
| `answer_priority` | `WHEN_MENTIONED` | Full context via periodic info-only batches; only reply when mentioned/threaded. Set only when the user wants context without AI judgment calls. |
| `answer_priority` | `WHEN_MENTIONED_ONLY` | No ambient context at all; only reply when mentioned/threaded. Set only on explicit request. |
| `answer_priority` | `ALWAYS` | Reply to (almost) everything immediately. Set only on explicit request. |
| `relevance_factor` | `1..99` | Bias for `ASK_AI`'s 1-5 scorer; `50` = neutral. `<=20` very reluctant (only unambiguous mentions get 4-5), `21-40` reluctant, `41-60` neutral (default), `61-80` proactive (plausible-but-not-certain relevance already gets 3-4), `>80` very proactive (almost anything on-topic gets 3-4). |
| `relation` | German text | Who you are, your role, your personality summary, and room behavior. |
| `<names>/<name>` | strings | All common name spellings, including first name only and lowercase. |

## Procedure

1. Determine the current room ID from the inbound Matrix event context.
2. Read `~/.hermes/profiles/<active_profile>/RELEVANCE_CONTEXT.xml`.
3. If the room entry does not exist, wait for the next message; the gate will auto-create it.
4. Otherwise, modify the existing `<room>` entry.
5. Keep the XML valid: preserve `<?xml?>`, `<relevance_context>`, `<rooms>`, and the `<room>` tag structure.
6. Write the file back.
7. Tell the user you updated your room behavior; you may briefly quote what changed.

## XML Schema

A room entry looks like this:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<relevance_context>
  <rooms>
    <room id="!xxxxx:server" alias="#alias:server">
      <answer_priority>ASK_AI</answer_priority>
      <relevance_factor>50</relevance_factor>
      <relation>Name / Rolle ... Funktion ... Verhalten in diesem Raum ...</relation>
      <names>
        <name>Charlotte</name>
        <name>charlotte</name>
        <name>Charlotte AI</name>
        <name>charlotte ai</name>
        <name>CharlotteWeiss</name>
        <name>charlotteweiss</name>
      </names>
    </room>
  </rooms>
</relevance_context>
```

`relation` must contain: name/role of the agent, function, personality summary, and concrete behavior for this room.

## Pitfalls

- Matrix lifecycle events are not conversational turns. Redactions (`m.room.redaction` / `[DELETE:<event>]`), reactions, peer gateway restart/online notices, typing/read-receipt changes, and comparable administrative events should be discarded deterministically before `ASK_AI` whenever the adapter exposes them. They must not consume relevance-model or main-agent tokens.
- In `ASK_AI`, score 1 remains buffered for its configured delay (120 seconds by default) and is then dispatched through the normal agent path. A later event may cancel and replace that timer according to its own score. Do not reinterpret score 1 as a deterministic drop; lifecycle/system events must instead be excluded before scoring.
- A Redaction must remove the redacted original from both the rolling transcript and any pending buffer, cancel the room timer when that buffer becomes empty, emit a body-free audit decision such as `matrix_redaction_lifecycle`, and return before mention checks, scoring, or dispatch.

- The filter maintains a short rolling room transcript (latest ~10 messages, including the agent's own outbound messages) to give the small relevance scorer conversational context. This helps it recognize that a short message like "nochmal" refers to the agent's previous turn, even when it is not an explicit @-mention or Matrix reply.
- `<names>` matching is case-insensitive, but list common variants explicitly anyway.
- Both `WHEN_MENTIONED` and `WHEN_MENTIONED_ONLY` react to `<names>` entries as if they were @-mentions, to replies to a message you were mentioned in, and to replies to your own previous messages (a user replying to your output is effectively addressing you, even without a mention).
- `WHEN_MENTIONED` still buffers and eventually delivers everything else, tagged as "nur zur Info, du brauchst nicht zu antworten" — treat those as read-only context, don't reply to them.
- `WHEN_MENTIONED_ONLY` never delivers non-mention messages at all.
- `ASK_AI` ignores `<names>` for immediate flushing; it uses the AI score and `relevance_factor` instead.
- The info-only flush delay for `WHEN_MENTIONED` defaults to 300s (`MATRIX_INTELLIGENT_REACTION_INFO_DELAY` env var) and is extended while someone keeps typing.
- "Read/mitlesen everything" is not a request for `ALWAYS`. `ASK_AI` and `WHEN_MENTIONED` both give you full context of every message; they just don't turn every message into a reply. Reserve `ALWAYS` for an explicit "reply to everything" request.
- If the room already has no entry and you must auto-generate one (or the user's wish is vague), default to `ASK_AI` with `relevance_factor` `50`, never to `ALWAYS`/`WHEN_MENTIONED_ONLY`.
- OpenRouter credentials for the relevance scorer are strictly profile-scoped. A profile without its own key must never fall back to `~/.hermes/.env` or another profile's credential; it must fail open and pass the message through.
- Primary and backup model IDs on the same OpenRouter account are not an independent provider fallback. An account-wide HTTP 402/payment error must immediately fail open with a visible "nicht einschätzbar, wird durchgeleitet" rationale instead of retrying another model against the same depleted account.
- If you break the XML structure, the filter will fall back to defaults. Validate that the file still parses after writing.
- Do not put credentials, API keys, or secret instructions into `relation`.

## Verification and ignored-message diagnosis

- Read `RELEVANCE_CONTEXT.xml` to confirm the `<room>` entry reflects the requested change.
- Ask the user to send the next message and check if the new behavior applies.
- Every inbound Matrix event is audited without message bodies in the active profile's `logs/matrix-relevance-decisions.jsonl`; the previous file is retained as `.jsonl.1` after 10 MiB rotation. Files are mode `0600`.
- Correlate by `event_id`/`correlation_id` and inspect `room_mode`, `relevance_factor`, `explicit_mention`, `explicitly_addressed_elsewhere`, `decision`, `reason_code`, `score`, `provider`, `model`, `fallback_used`, `fail_open`, `dispatch_attempted`, `dispatch_result`, and `error_class`. A successful handover also records `agent_turn_started` and `agent_turn_completed`; `agent_session_id` is currently `null` because the gate API does not return one.
- For an apparently ignored message, diagnose the chain in this order: decision record present → room mode/rule → score/provider/fallback or fail-open → dispatch attempt → dispatch result/agent turn. Only edit `RELEVANCE_CONTEXT.xml` after the audit proves a classification or rule problem. Do not reflexively "sharpen" relation/names when the failure is ingestion, E2EE, dispatch, or agent execution.
- If no decision record exists, inspect Matrix ingestion/decryption and the profile gateway journal. If a decision exists but no dispatch result follows an attempt, inspect the gateway process and agent execution path.
- Audit write failures are fail-open and appear as `Matrix IR: decision audit failed` in the profile gateway journal.
