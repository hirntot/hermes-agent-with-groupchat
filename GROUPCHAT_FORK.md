# Hermes Agent with Groupchat Coordination

This experimental downstream build lets multiple independently running Hermes
profiles participate in the same external group conversation without replying
to every message or triggering each other indefinitely.

It adds one opt-in, transport-independent conversation boundary with two parts:

- inbound relevance routing, including explicit mentions, delayed scoring,
  per-room context and coordinated voice-note transcription;
- outbound pingpong prevention for acknowledgements, intentional silence,
  duplicate replies and Hermes lifecycle messages.

The feature is configured per profile in the authenticated Hermes dashboard.
English and German UI text is included. Editable multilingual phrase and regex
lists allow administrators to inspect and adapt deterministic decisions.

## Status

- Fork base: Hermes Agent 0.21.0, upstream commit
  `63279301bcbdc185c1b07b98a9312eb0c862f26d`
- Addon version: `0.1.0-experimental`
- Live-tested transport: Matrix
- Integration tests: 118 focused Groupchat, conversation-policy and delivery
  ledger tests, plus the adjacent Matrix test suites used during development
- Telegram, Slack and Mattermost use the normalized middleware boundary but
  have not yet received equivalent production live testing

This publication is a reproducible snapshot of the deployed downstream build,
not yet a minimal upstream pull request. It contains supporting gateway and
Matrix robustness changes required by the tested deployment. A later upstream
submission should be split into smaller reviewable changes.

## Safety and consent model

Groupchat is disabled unless an administrator enables it for a profile and
selects a transport. Receiving unmentioned Matrix messages additionally
requires the transport's effective `require_mention` setting to be `false`.

Operators should make every participating account visibly identifiable as a
bot. The addon does not weaken transport authorization or room allow-lists.
Deterministic lifecycle filters, bounded delays, duplicate suppression and a
message-storm circuit breaker reduce feedback-loop risk. Gateway shutdown and
restart notices are non-durable and are never replayed as conversation turns.

Decision logs are private local JSONL files with stable reason codes. They omit
message bodies, prompts, credentials and free-form model rationales. The UI
shows the directory in which they are stored so operators and authorized agents
can understand why a message was delivered, delayed or discarded.

## Configuration

Read [`plugins/groupchat/README.md`](plugins/groupchat/README.md) for the full
configuration schema, dashboard behavior, message-pattern semantics, decision
log paths and transport boundary.

A minimal profile configuration is:

```yaml
plugins:
  enabled: [groupchat]

groupchat:
  enabled: true
  platforms: [matrix]
  relevance:
    enabled: true
  pingpong_guard:
    enabled: true

platforms:
  matrix:
    require_mention: false
```

Exact platform configuration keys can differ between legacy environment-based
and current YAML installations. The Groupchat dashboard displays the saved
effective Matrix participation state.

## License and upstream

Hermes Agent and this downstream modification are distributed under the MIT
license in [`LICENSE`](LICENSE). The upstream project is
[`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent).
This repository is not endorsed by Nous Research.
