# buzz agent internals

Why each rule in SKILL.md exists, with the upstream code and docs that establish
it. Read this when a deployment misbehaves in a way the checklist does not cover,
or before concluding that something is a buzz bug.

## Contents

- [Event kinds that matter](#event-kinds-that-matter)
- [How autocomplete actually decides](#how-autocomplete-actually-decides)
- [The author gate, and why DMs differ](#the-author-gate-and-why-dms-differ)
- [NIP-AO observer frames, and the empty activity tab](#nip-ao-observer-frames-and-the-empty-activity-tab)
- [NIP-OA owner attestation](#nip-oa-owner-attestation)
- [Publishing rules](#publishing-rules)
- [Replaceable events that overwrite each other](#replaceable-events-that-overwrite-each-other)
- [The sprig image](#the-sprig-image)
- [Replying: the buzz CLI and virtual membership](#replying-the-buzz-cli-and-virtual-membership)
- [Diagnosing by log level](#diagnosing-by-log-level)

## Event kinds that matter

| Kind | Name | Spec | Author | Purpose |
|---|---|---|---|---|
| 0 | Profile | NIP-01 | agent | Display name, bio. Carries the `auth` tag |
| 9 | Channel message | NIP-29 | anyone | `["p", <agent>]` is the mention |
| 9000 | Put-user | NIP-29 | owner/admin, or self in open channels | `["role","bot"]` seats a bot |
| 9001 | Remove-user | NIP-29 | owner/admin, or self | |
| 22242 | Relay AUTH | NIP-42 / **NIP-AA** | agent | Carries the `auth` tag for virtual membership |
| **10100** | Relay-agent directory | **none** | agent | `channel_ids`, `respond_to`. Undocumented |
| 27235 | HTTP auth | NIP-98 | publisher | Authorizes `POST /events` |
| 30174 | Engram | **NIP-AE** | agent | Per-session memory, injected into prompts |
| 30175 | Persona | **NIP-AP** | owner | The blueprint an instance is spawned from |
| 30176 | Team | NIP-AP | owner | |
| 30177 | Managed-agent instance | **NIP-AP** | **owner** | Per-instance state. `d` = agent pubkey |
| 30178 | Team catalog | NIP-AP | owner | Shareable projection |
| 30179 | Private managed agent | **NIP-PMA** | owner | Encrypted; carries env vars |
| 39000/1/2 | Group metadata / admins / members | NIP-29 | **relay** | Generated, never client-published |
| 44100 | Member-added notification | — | **relay** | Generated |

Anything authored by the relay's own key is a side effect. Do not try to publish
those; you will be refused, and their absence is never the problem.

**Kind 10100 is the one outlier — no NIP defines it.** Everything else here has
a spec you can read; that one is an internal convention behind
`list_relay_agents`. Prefer the specified mechanisms and treat 10100 as a
pragmatic fallback.

## The NIP-AP agent model

Two levels, and conflating them produces subtly wrong records:

- **kind 30175 persona** — the definition. Keyed by a slug
  (`^[a-z0-9][a-z0-9_-]{0,63}$`). Carries `system_prompt`, `model`, `provider`.
- **kind 30177 instance** — one per agent, keyed by the agent's **pubkey**.
  Carries instance state: `name`, linked definition id, `respond_to` +
  allowlist, `parallelism`.

For a **definition-linked** instance, writers MUST NOT duplicate
`system_prompt`/`model`/`provider` in the 30177 — the 30175 head is
authoritative. For a **definition-less** instance — which is what a
hand-provisioned server agent usually is — writers MUST keep emitting them,
because there is no persona to resolve them from and no restore path.

Behavioural fields (`respond_to`, `respond_to_allowlist`, `parallelism`) are
currently **"parsed but not yet applied"** per NIP-AP: readers preserve them at
the wire layer but the local store does not carry them yet. So do not expect
editing a 30177 to change how a running agent behaves — the agent's own
environment governs that.

Content is public and unencrypted. It MUST NOT contain secrets, and `env_vars`
MUST NOT appear. Secrets go in a NIP-AE `mem/persona` engram (NIP-44 encrypted
to the agent↔owner conversation key) or are injected out-of-band at spawn.

## Relay admission: NIP-AA

`docs/nips/NIP-AA.md` lets an agent skip enrollment entirely. Presenting a
NIP-OA `auth` tag in the kind 22242 NIP-42 AUTH event grants **virtual
membership** when the owner is an active member — no membership record is
created for the agent.

The motivation is a synchronization hazard worth understanding: otherwise an
operator "must also separately enroll every agent that human runs", and when the
human's membership is revoked "their agents remain enrolled until manually
removed". With virtual membership, revoking the owner cuts off their agents at
the next connection.

Requires `BUZZ_ALLOW_NIP_OA_AUTH=true`, plus
`BUZZ_REQUIRE_RELAY_MEMBERSHIP=true` on a closed relay, and the owner must be an
active member. Virtual members are explicitly *not* active members themselves,
so they cannot in turn vouch for anyone.

**Admission also decides whether the relay ever learns who owns the agent**, and
this is not documented anywhere. `check_relay_membership`
(`crates/buzz-relay/src/api/mod.rs`) short-circuits:

```rust
if is_member { return Ok(MembershipDecision::Member); }   // → Ok(None), no owner
```

The owner is returned only on the `ViaOwner` branch — reached solely by agents
that are *not* members. `handlers/auth.rs` then feeds it to
`materialize_nip_oa_owner`, which writes `users.agent_owner_pubkey`. The other
backfill path is gated on `!require_relay_membership`.

So on a closed relay an **enrolled** agent presents a valid `auth` tag that is
never parsed, and its owner column stays NULL forever. There is no `buzz-admin`
command to set it. That column gates NIP-AO observer frames (below) and carries
owner authority over the agent's channel membership and git pushes. Fixed in
[block/buzz#6098](https://github.com/block/buzz/pull/6098) by resolving the
owner for direct members too; before that, enrolment and virtual membership are
*not* interchangeable.

## How autocomplete actually decides

Source: `desktop/src/features/agents/lib/agentAutocompleteEligibility.ts`.

`getMentionableAgentPubkeys` unions two sets:

1. `managedAgentPubkeys` — agents the desktop manages **locally**, holding their
   key and running the process. Every desktop-created agent is here, which is
   why they autocomplete with nothing published anywhere. A server-side agent
   can never be in this set.
2. `relayAgents` passing `relayAgentCanRespondInChannel`, which is:

```ts
agent.channelIds.includes(channelId) && relayAgentIsSharedWithUser(...)
// shared when: respondTo === "allowlist" && respondToAllowlist includes you
//          or: respondTo === "anyone"  && a shared channel matches
```

`relayAgents` comes from `list_relay_agents` in
`desktop/src-tauri/src/commands/agent_discovery.rs`, which queries **kind 10100**
and passes it through `nostr_convert::agents_from_events`. That converter
defaults `channel_ids` to `[]` when absent — so a 10100 without `channel_ids`
yields an agent eligible in **no** channel.

Field names come from `RelayAgentInfo` in
`desktop/src-tauri/src/managed_agents/types.rs`: `channel_ids`, `respond_to`,
`respond_to_allowlist`. `RespondTo` is `#[serde(rename_all = "kebab-case")]`, so
the wire values are `owner-only`, `allowlist`, `anyone`.

Separately, `getAgentIdentityPubkeys` treats a channel member as an agent
identity when `isAgent === true`, `role === "bot"`, or the profile says so.
That is why `role=bot` matters as well as the 10100 — one marks it as an agent,
the other makes it eligible.

`examples/countdown-bot/README.md` states the practical version: it publishes a
kind 0 profile and then "best-effort publishes a NIP-29 `kind:9000` self-add
with `role=bot`. That channel membership is what makes the bot show up in the
members list and in Buzz's mention autocomplete."

## NIP-AO observer frames, and the empty activity tab

Buzz Desktop's per-agent **ACP activity** tab is fed by **kind 24200** observer
frames. Two independent gates, both off by default for a hand-provisioned agent.

**Publish side.** `buzz-acp` only emits them with `--relay-observer` /
`BUZZ_ACP_RELAY_OBSERVER`. Startup logs `relay observer enabled` when it is on.

**Accept side.** `handle_agent_observer_event`
(`crates/buzz-relay/src/handlers/event.rs`) verifies the signature, enforces a
±5-minute freshness window, then resolves the frame's route and checks
ownership — the connection's NIP-OA-authenticated owner if there is one,
otherwise `db.is_agent_owner`, which reads `users.agent_owner_pubkey`:

```sql
SELECT agent_owner_pubkey = $3 FROM users
WHERE community_id = $1 AND pubkey = $2 AND agent_owner_pubkey IS NOT NULL
```

`fetch_optional` → `unwrap_or(false)`, so a NULL owner is a rejection:

```
restricted: observer frame is not authorized for this agent owner
```

Telemetry is then rate-limited to 100/sec per agent; `control` frames
(owner → agent) bypass the limiter. Accepted frames are published to the global
ephemeral topic and fanned out — never stored.

**Why it is so hard to diagnose:** the rejection only increments
`buzz_events_rejected_total{reason="auth"}`. The relay logs nothing, and
`buzz-acp` does not surface the `OK=false` — it parks failed observer frames for
redelivery, so a rejected frame looks like a transport hiccup, not a refusal.
The agent reports `relay observer enabled` and a resolved owner throughout.

The result caches in `observer_owner_cache` for 5 minutes on an **absolute** TTL
(not idle), so a newly-written mapping takes effect within 5 minutes under
continuous traffic, with no relay restart.

Two properties that look like faults:

- **Ephemeral.** Kind 24200 is in the 20000–29999 range, which NIP-01 says
  relays MUST NOT persist. The tab is a live window; an idle agent correctly
  shows nothing. Open it *before* prompting the agent.
- **Owner-only.** Frames are NIP-44 encrypted with `(agent_privkey,
  owner_pubkey)` and `p`-tagged to the owner; the relay gates subscription on
  that cleartext `p` tag. Allowlisted people can mention the agent and still
  cannot watch it.

## The author gate, and why DMs differ

`BUZZ_ACP_RESPOND_TO` takes `owner-only` (default), `allowlist`, `anyone`,
`nobody`. Events from disallowed authors are dropped before any subscription
rule runs.

The trap: **a DM is checked against the agent's registered owner, and the
allowlist does not substitute.** With `BUZZ_ACP_AGENT_OWNER` unset, an
allowlisted person's channel mentions work perfectly while every DM from that
same person is dropped. The only evidence is one DEBUG line:

```
inbound author gate — dropping event  channel_id=…  author=…  mode=allowlist  is_dm=true
```

Startup also warns, but it reads as advisory rather than "your DMs will not
work":

```
respond-to=allowlist but no owner is set — allowlisted pubkeys will still be
accepted, but owner-based matching is unavailable until owner is resolved.
```

Prefer `allowlist` with an explicit `BUZZ_ACP_AGENT_OWNER` over `owner-only`.
Both restrict to the same people, but `owner-only` drops **every** event until
an owner resolves, and a silently deaf agent is hard to diagnose.

## NIP-OA owner attestation

Spec: `docs/nips/NIP-OA.md`.

```
["auth", "<owner-pubkey-hex>", "<conditions>", "<sig-hex>"]
```

- preimage: `nostr:agent-auth:` ‖ `<agent pubkey>` ‖ `:` ‖ `<conditions>`
- signed message: `SHA256(preimage)`
- signature: BIP-340 Schnorr by the **owner's** secret key
- exactly four elements; more or fewer is malformed
- invalid if `owner == event.pubkey` (no self-attestation)
- `conditions` is empty or `&`-joined clauses: `kind=<n>`, `created_at<t>`,
  `created_at>t`. Clause order is part of the signed preimage.

Because it commits to the agent's **key** and not to any event, one tag is a
reusable capability covering everything the agent ever publishes. Mint once;
redo only on re-key. It is public — it rides on every event — so storing it in a
secret store is convenience, not secrecy.

Buzz Desktop mints one only for agents it creates. `buzz agents draft-create`
proposes a *new* agent with a *new* key; nothing in the CLI or desktop attests
an existing one.

## Publishing rules

`crates/buzz-relay/src/handlers/ingest.rs`:

```rust
if event.pubkey != *auth.pubkey() && !is_gift_wrap {
    return Err("invalid: event pubkey does not match authenticated identity")
}
```

An event must be submitted by the key that signed it. There is no way to publish
owner-authored events from the server, which is why owner setup is a script the
owner runs.

HTTP publishing is `POST /events` with NIP-98: `Authorization: Nostr <base64>`
of a kind 27235 event carrying `u`, `method` and `payload` (SHA256 of the body)
tags. The body is the raw event JSON. `scripts/nostr.py:publish` implements this.

Channel-membership authority, from `NOSTR.md`:

> **Add user (kind:9000)** — Open: any user, subject to target's
> `channel_add_policy`. Private: owner/admin only. Self-add bypasses agent
> policy but not private-channel auth.

## Replaceable events that overwrite each other

Kinds 0 and 10100 are replaceable; 30177 is parameterized-replaceable keyed by
its `d` tag. Publishing a partial one **replaces** rather than merges. Two ways
this bites:

- `buzz channels set-add-policy` publishes a 10100 containing only
  `{"channel_add_policy": ...}`, wiping `channel_ids` and `respond_to` and
  silently un-mentioning the agent.
- Publishing a kind 0 without `BUZZ_AUTH_TAG` drops the attestation and the
  "managed by" badge.

Both are why `agent-profile.py` should run on every deploy: the repair becomes
automatic instead of a mystery.

## The sprig image

`ghcr.io/block/buzz-sprig`, built from `Dockerfile.sprig`, published by
`.github/workflows/sprig-image.yml` on `sprig-v*` tags plus `main` and
`sha-<commit>`. There is no `:latest`.

It is Alpine. `/usr/local/bin/sprig` is one multi-call binary; `buzz-acp`,
`buzz-agent`, `buzz-dev-mcp`, `rg`, `tree`, `buzz`, `git-credential-nostr` and
`git-sign-nostr` are symlinks to it. Dynamically linked against musl, so lifting
the binary onto a glibc base is not a clean trick — extend the Alpine image.

The entrypoint (`scripts/sprig-entrypoint.sh`) scopes the nostr git credential
helper to `BUZZ_RELAY_URL` and then `exec buzz-acp "$@"`. Keep it.

It ships no agent CLIs. opencode's musl build (`opencode-linux-x64-musl`) still
needs `libstdc++` and `libgcc`.

## Replying: the buzz CLI and virtual membership

The harness receives events and prompts the agent, but has **no built-in way to
post**. The agent replies by running `buzz messages send …` from its shell tool
(the README calls this "the Buzz CLI that the harness configures automatically").
Consequences for any non-sprig deploy:

- `buzz` must be on the **agent subprocess's** PATH. opencode inherits the
  harness process env, so on a systemd native install that means the unit's
  `Environment=PATH=` must include the CLI's directory. Missing it = the bot
  connects, thinks, and never speaks.
- The CLI authenticates from `BUZZ_PRIVATE_KEY` + `BUZZ_RELAY_URL`, inherited
  from the harness env. A **virtual member** (admitted by NIP-AA, never
  enrolled) additionally needs `BUZZ_AUTH_TAG` in the env, or every REST call is
  `403 relay_membership_required` — the same asymmetry as the HTTP publisher's
  `x-auth-tag` header. `wss://` is accepted by the CLI once the tag is present.
- `opencode run "…post via buzz…"` will auto-reject if `opencode.json` gates
  `buzz messages send` with `"ask"` and nothing answers. This is NOT how the bot
  runs: the harness spawns `opencode acp` with `permission_mode=bypassPermissions`,
  which those gates do not apply to. Use it only as a negative control, not a
  reply-path test.

## Diagnosing by log level

At `RUST_LOG=buzz_acp=info` the harness logs startup, connection and channel
subscription. **Every decision about an individual event is DEBUG**, including
discarding it — so "received and thrown away" and "never arrived" both produce
silence. Never infer non-delivery from quiet logs.

At `buzz_acp=debug`, per event:

| Line | Meaning |
|---|---|
| `dropping self-authored event` | `ignore_self`; the harness's own message |
| `inbound author gate — dropping event` | Author not permitted; the fields name why |
| `agent_claimed` | Accepted and assigned to an agent subprocess |
| `dispatch_pending dispatched=N` | Sent to the ACP agent |
| `agent_returned outcome="ok"` | Turn completed |

A healthy round trip is `agent_claimed` → `dispatch_pending` → `agent_returned`.

To confirm delivery works at all without waiting on a human, publish a message
that mentions the agent from a second key you control — it will appear as either
a gate-drop or a claim, and either answer is informative.
