# Groupchat

One opt-in Hermes addon for inbound relevance routing and outbound pingpong
prevention. The filter-model selection is shared by both directions. It does
not add model tools or change the agent's cached system prompt.

This addon was developed by [RechnerLotsen](https://rechnerlotsen.com/).
RechnerLotsen builds tools like this for its own background workflows; customer
computer support remains personal and is provided by real people.

> **Validation scope: Matrix only.** This addon has been live-tested exclusively
> with Matrix. Telegram, Slack, Mattermost and other selectable transports are
> architectural integration points that have not yet been validated in live
> group chats; they are not currently claimed as supported Groupchat transports.

## Configuration

Enable `groupchat` through Hermes's existing plugin manager. In the dashboard,
open **Groupchat**, select the target profile, configure the feature and save.
The page explicitly targets its selected profile (independent of the host's
other management views). Saving an enabled configuration also enables the
native plugin for that profile. Restart that profile's gateway to apply it.

The dashboard page follows the host's existing language selector through the
plugin SDK's `useI18n` hook. English is the default and fallback language;
German translations are included. Other locales currently fall back to English.
There is no separate Groupchat language preference. Labels, hints, placeholders,
accessibility names and save-status messages update when the host language changes.

### Editable message patterns

The **Message patterns** section edits the actual per-profile runtime rules:
`relevance.system_patterns`, `relevance.multiline_patterns`,
`relevance.literal_phrases` and `pingpong_guard.silence_patterns`.
Each regex occupies one line; commas and quantifiers are preserved. Blank lines
are ignored on save. Invalid expressions are rejected with a list/line diagnostic
before configuration is written. Lines beginning with `#` (after optional leading
whitespace) are comments in all lists: they are preserved but never evaluated.
For example, `# English keywords` and `# German keywords` can group rules.
Regex patterns can use `\#` to match a leading hash character. Audit rule indices
refer to the saved list positions, including comment lines.
Each field has a **Restore defaults** action;
restoring changes the draft and requires Save, like any other edit.

Custom lists replace defaults. An empty list disables that list, while an omitted
or null list uses the built-in defaults. Relevance regexes match the beginning of
the first normalized line, case-sensitively. The multiline detector requires the
first line and another line to match a system or additional multiline pattern.
The literal-text list contains one phrase per line, **not regex**. Without an
explicit mention, non-overlapping occurrences of any one listed phrase must cover
**more than 50%** of the message to discard it. Exactly 50% passes this check;
an explicit mention always bypasses this list. Matching is case-sensitive after
whitespace normalization of both phrase and message. Distinct phrases are not
added together. An empty list disables this check. The former `interrupt_notice`
setting is converted to a one-item list when settings are loaded; saving writes
only `literal_phrases`. Separate system-message regex/lifecycle rules retain their
own behavior and are not exempted by this literal-list mention exception.
Pingpong regexes search case-insensitively in raw and Markdown-stripped replies
before the length threshold and model check. Anchors `^...$` match whole replies.
A short pattern-matched reply is allowed when the immediately preceding English or German
message explicitly requests visible output (for example, “reply exactly with
ok” or “antworte exakt mit ok”). Negated requests such as “do not reply” and
“nicht antworten” do not trigger this exception.

Empty lists do not disable AI checks, empty-output suppression, duplicate/voice
coordination, or structural edit/delete lifecycle handling. These editors accept
trusted administrator regexes, not untrusted message content; complex expressions
can be expensive. Validation checks syntax/size, not computational complexity.

All behavior lives under `groupchat` in that profile's `config.yaml`:

```yaml
plugins:
  enabled: [groupchat] # retain any other enabled plugins
groupchat:
  enabled: true
  platforms: [matrix, telegram, slack]
  filter_model:
    provider: mistral
    model: mistral-small-latest
    fallbacks:
      - provider: openrouter
        model: mistralai/mistral-small-3.2-24b-instruct
      - provider: openai-codex
        model: "" # use this profile's Codex model
  relevance:
    enabled: true
    # Score 0 is discarded; score 1 is passive context without a timer.
    score_delays: {5: 0, 4: 3, 3: 10, 2: 30}
  pingpong_guard:
    enabled: true
    min_chars: 60
```

Credentials continue to use the existing provider credentials/OAuth in the
active Hermes profile. Neither the dashboard nor the configuration contains
copies of keys. The model ID is freely editable, not a frozen catalog.

The relevance model always scores only the newest message. Recent room
messages are supplied as context, never merged into one item for scoring.
Score `0` is discarded. Score `1` has no delivery timer and remains passive
context until a score `2`–`5` message (or a direct address) starts an agent
turn. That turn receives the retained context; successful dispatch consumes
it. Explicit conversations addressed to another local Groupchat profile use
the same score-1 context lane. This passive context survives gateway restarts:
it is atomically stored below `HERMES_HOME/groupchat` in
`passive-context.<channel>.json` and removed only after successful dispatch.

Native transport authorization, allowed-room rules, bot admission and
mention-only policies are not loosened by this addon. To score unmentioned
group messages, the transport must already forward those messages (for example,
`require_mention: false`). No addon can process messages a platform does not
deliver to its bot. The UI discovers channel choices from Hermes's platform
registry, including Mattermost and third-party platforms. Selecting a channel
does not configure or connect it.

### Matrix prerequisite: `require_mention=false`

For an agent to participate through relevance scoring when it was not directly
mentioned, its effective Matrix setting must be `require_mention=false`
(`MATRIX_REQUIRE_MENTION=false` is the legacy environment form). With `true`,
direct mentions and outbound pingpong protection still work, but ordinary room
messages never reach Groupchat's relevance filter. The dashboard's **Matrix
participation** section lists every running named gateway and shows its saved effective
`require_mention` value and whether it is an active relevance participant.
This is a configuration view, not proof that the gateway process has restarted;
saved changes become effective after that profile's gateway restart.

## Behavior and migration

The relevance engine retains the existing Matrix implementation: mention
flushes, score-based buffering, XML room context, status-message exclusion,
voice coordination, and duplicate suppression. Control commands and internal
events bypass relevance routing. Native Matrix typing/edit/delete normalization
stays in the Matrix adapter. Not every other transport emits the same lifecycle
signals, so identical typing/voice-coordination behavior is not claimed there.

The outbound guard retains deterministic silence/acknowledgement patterns and
the short-message model check. At or above `min_chars`, replies skip the model
check. Worker failures/timeouts fail open; workers are terminated on timeout or
cancellation. Failed sends roll back duplicate state so retries can deliver.
With the guard enabled, live response streaming is disabled on every selected
platform, including Matrix, so a draft cannot publish text before classification.

`config.migrate_legacy(config, env)` is an explicit, idempotent, one-time
conversion. It copies only allowlisted behavioral settings, never credentials.
The deployed addon reads Groupchat configuration, not `MATRIX_*` settings.
The old Matrix module path remains a compatibility import for existing tests
and tooling, not a second production configuration surface.

Conversation identity is normalized as platform, chat, workspace/scope and thread.
Matrix threads have independent rolling transcripts and buffers as well.
Matrix keeps its existing `RELEVANCE_CONTEXT.xml` for room configuration;
other platforms namespace context files by platform/conversation.

## Languages and decision logs

Source comments, runtime instructions, generated status text and model prompts
are English. German input markers remain supported for compatibility with older
messages. UI translations and message-matching vocabulary are intentionally
multilingual. Pingpong defaults cover German and English acknowledgements such
as `verstanden` / `understood`, `erledigt` / `done`, `danke` / `thanks` / `thank you`.
Additional languages can be added as regex lines in **Message patterns**.
Existing custom lists are not overwritten by new defaults: use **Restore defaults**
to adopt the updated list, or add only the desired expressions manually.

The **Decision logs** UI section shows the absolute log directory for the selected
profile (`HERMES_HOME/logs`), updated when the profile changes. It does not list
each channel separately: filenames identify the channel and filter. This directory
is on the gateway server, not the browser computer. For example:

- `HERMES_HOME/logs/matrix-relevance-decisions.jsonl` — inbound decisions,
  score/delay, routing reasons and dispatch/agent lifecycle records.
- `HERMES_HOME/logs/matrix-groupchat-outbound.jsonl` — outbound filter decisions,
  including pingpong and relevance-engine duplicate suppression.

Replace `matrix` with `telegram` or `slack` for those transports. Each file is
created on its first recorded decision, is owner-readable/writable only (`0600`),
and rotates at 10 MiB to `.jsonl.1`, retaining one previous file. Old records are
therefore not retained indefinitely. Log-write failures never block delivery.
Agents can read these files with their normal authorized file tools; the addon
does not grant additional file permissions or expose log contents over its API.

Every record includes a UTC timestamp, platform/profile, room identifier,
decision and stable English `reason_code`. Inbound entries include event IDs
and available score/model information. Outbound attempts get a `decision_id`
and a scope identifier. A `send` decision means the filter allowed the reply,
not that the transport successfully delivered it.

Examples of reasons:

- `system_message_before_routing`: an inbound system/progress rule matched.
- `literal_phrase_majority`: without an explicit mention, one phrase occupied
  more than half of the message (older logs may say `interrupt_notice_majority`).
- `pattern_match`: an outbound deterministic pattern matched.
- `model_suppress` / `model_send`: the configured model's classification.
- `length_threshold`: the reply bypassed the short-reply model check.
- `duplicate_reply` / `duplicate_transcription`: repeated outbound content.
- `model_failure_fail_open`, `worker_timeout_fail_open`, `worker_failure_fail_open`:
  the check failed and the reply was allowed through.

Pattern matches record a one-based rule index and SHA-256 fingerprint of the
pattern (plus its collection for inbound rules), allowing comparison with the
configured regex without copying the regex itself into the log. If rules change,
the fingerprint distinguishes different rules at the same position. Model
classifications include available provider/model and fallback metadata.
Message bodies, prompts, credentials, tokens and free-form model rationales are
deliberately excluded. Logs still contain room/event identifiers and are private
operational data, not something to publish with an upstream contribution.

The restart-durable score-1 state is deliberately different from the decision
logs. `HERMES_HOME/groupchat/passive-context.<channel>.json` contains the retained
message text, sender label, timestamp and available event/reply identifiers because
that context must be delivered after a restart. The directory uses mode `0700` and
the state file mode `0600`; writes are atomic. Treat it as private conversation
data. The dashboard displays the directory for the selected profile, and the file
is deleted when no retained context remains.

For example, on the gateway server:

```sh
tail -n 50 "$HERMES_HOME/logs/matrix-groupchat-outbound.jsonl"
tail -n 50 "$HERMES_HOME/logs/matrix-relevance-decisions.jsonl"
```

## Host boundary

`gateway/conversation.py` is a generic middleware boundary. Native plugins
register an adapter-local factory using
`ctx.register_middleware("gateway_conversation", factory)`.
Each returned object supports:

- `bind(dispatch)`, `receive(event)` for normalized incoming messages;
- `send(chat_id, content, reply_to, metadata, *, send)` for outbound text;
- `processing(event, phase, session_id, outcome)`, `typing(chat_id)`, `close()`;
- `delays_messages` and `buffers_output` capability properties.

Factories and middleware instances must not be shared between profiles or
adapters. `bind` supplies the next ingress continuation; `send` supplies the
next egress continuation. The host bypasses control commands independently of
plugin policy. The concrete Groupchat policy lives entirely in this directory.

The base adapter automatically wraps text sends, edits, drafts and media captions,
including inherited methods. No Groupchat-specific decorators are needed in native
adapters. Suppressed captions do not discard attachments. An edit uses the cached
original conversation when available; after restart, edits without explicit
context can only use the active conversation or room-level context.
The standalone tool/cron sender applies the same middleware before splitting
messages or uploading files; it does not share the live gateway's in-memory history.

Transport adapters remain responsible for translating native mention, thread,
scope and lifecycle semantics. Mention metadata has been integrated for Matrix,
Telegram, Slack and Mattermost; other transports require a semantic audit before
claiming equal inbound behavior. Direct SDK calls outside the standard adapter
contract (for example custom rich cards) are not covered by automatic text wrapping.

The dashboard extension uses Hermes's existing authenticated plugin API and
React SDK. It mounts under `/groupchat`; API routes are scoped to the explicitly
selected profile and only return/write Groupchat settings plus plugin activation.

## Verification

Run `scripts/run_tests.sh tests/gateway/test_conversation_policy.py` plus the
`tests/gateway/test_conversation_boundary.py`, Matrix, Slack, Telegram, Mattermost,
base-adapter, standalone send-message and streaming regression suites.
The addon tests exercise real plugin discovery, common ingress/egress, the
actual guard subprocess, configured-model HTTP requests with a fake provider,
profile/thread isolation, settings validation/migration, and dashboard saves.

This is an upstream-oriented local port, not an upstream submission. Local
Matrix authorization/outbox/E2EE patches are intentionally outside the addon.
The inherited burst policy still exits the gateway on a message storm; this
and broader native lifecycle/relay coverage deserve an explicit upstream review.
