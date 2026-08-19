---
name: buzz-agent-deploy
description: >-
  Deploy an always-on buzz (block/buzz Nostr relay) agent on a server as a
  Docker container, running opencode — or goose, Codex, Claude Code — under the
  buzz-acp harness, and make it actually visible and @-mentionable in Buzz
  Desktop. Use this whenever the user wants a buzz agent that is not tied to a
  laptop, with phrasings like "run an agent on my server", "add a bot to buzz",
  "buzz agent in Docker", "self-hosted opencode agent", "make my agent
  mentionable", or "deploy buzz-acp". Also use it for DEBUGGING an existing
  buzz agent that misbehaves in the specific silent ways these deployments
  fail — the agent ignores DMs but answers in channels, never appears in @
  autocomplete or the Agents panel, shows no "managed by" badge, has a
  permanently empty ACP activity tab, sits idle reporting "discovered 0
  channel(s)", 404s on the relay WebSocket, or can be @-mentioned by some
  people but not others. Reach
  for it even if the user only describes the symptom and never says "buzz
  agent".
---

# Deploying a buzz agent on a server

An agent created inside Buzz Desktop runs on the laptop that pressed the button
and stops when the laptop sleeps. This skill builds one that lives on a server.

Upstream supports this explicitly. `docs/remote-agents.md` states that a script
exporting `BUZZ_PRIVATE_KEY` and `BUZZ_RELAY_URL` and exec'ing the harness "is a
conforming launcher at this layer — today, with no code change." A compose
service is a supported deployment, not a workaround.

## What a proper agent consists of

Follow the NIPs in `docs/nips/`, not `examples/countdown-bot`. That example is
useful but explicitly "deliberately boring" — it is not even an LLM agent, and
it publishes the bare minimum to speak in one channel. Treat it as the floor.

| Layer | Spec | Author | Required? |
|---|---|---|---|
| Own keypair | — | — | yes; the key is the unit of revocation |
| Owner attestation (`auth` tag) | **NIP-OA** | owner | **yes** — without it the UI reads "owner unavailable" |
| Relay admission | **NIP-AA** | — | yes; via attestation, or explicit `add-member` |
| kind 0 profile | NIP-01 | agent | yes; otherwise it is a hex string |
| **kind 10100 directory** | **none** | agent | **yes** — this is what makes it `@`-mentionable |
| kind 30177 instance record | **NIP-AP** | **owner** | yes for a real agent; Agents panel + conformance |
| kind 30175 persona | **NIP-AP** | owner | optional; a reusable blueprint |
| kind 9000 `role=bot` per channel | NIP-29 | owner, or self in open channels | optional; members-list presentation |
| Channel membership (any role) | NIP-29 | self in open channels | yes, or it sees nothing |
| kind 30174 engrams (memory) | **NIP-AE** | agent | handled by buzz-acp |
| Turn metrics | NIP-AM | agent | handled by buzz-acp |
| **Live activity feed** | **NIP-AO** | agent | **opt-in, and gated twice** — see [The activity tab](#the-activity-tab-and-the-two-gates-behind-it) |
| `BUZZ_ACP_AGENT_OWNER` | — | — | yes, or **every DM is dropped** |

The harness already covers the agent-side protocol — memory, metrics,
reactions, presence. What it does **not** do is the owner-side registration,
because those events must be signed by the owner's key.

### Definition-linked versus definition-less

NIP-AP's agent model has two levels: a **persona** (kind 30175, the blueprint)
and an **instance** (kind 30177, keyed by the agent's pubkey). A
hand-provisioned server agent is normally *definition-less* — no persona behind
it — and the spec is explicit about what that costs:

> **Exception — definition-less instances:** an instance with no linked
> definition is its own definition; writers MUST keep emitting the
> definition-level fields for such instances. […] a definition-linked instance
> self-heals from its definition at next spawn, but a definition-less one has no
> restore path.

So a standalone agent's 30177 should carry `model`, `provider` and
`system_prompt` alongside the instance fields. A definition-linked one must
*not* duplicate them — the persona is authoritative. `owner-setup.py` takes
`--persona-id` or the standalone fields and does the right thing either way.

The content body is public and unencrypted: **no secrets, and never
`env_vars`.** Secrets for an agent belong in a NIP-AE `mem/persona` engram or
out-of-band injection at spawn.

### The one that is not in any NIP is also the one you cannot skip

**kind 10100**, the relay-agent directory entry, appears in **no NIP**. It is an
internal convention behind Buzz Desktop's `list_relay_agents`. It is also, for a
server-side agent, *the* thing that makes `@` autocomplete work.

This was established by controlled comparison rather than reading, because the
docs point the other way:

| | agent A | agent B |
|---|---|---|
| kind 10100 | ✗ | **✓** |
| `role=bot` seating | ✓ | ✗ (plain `member`) |
| kind 30177 | ✓ | ✗ |
| NIP-OA `auth` tag | ✓ | ✗ |
| **`@`-mentionable** | **no** | **yes, immediately** |

So `role=bot` and kind 30177 are *not* what autocomplete reads, and 10100 alone
is sufficient. Note this contradicts `examples/countdown-bot`'s README, which
says the `role=bot` self-add "is what makes the bot show up in the members list
and in Buzz's mention autocomplete." That claim looks stale for current Buzz
Desktop, or true only of the members list. Trust the experiment; re-run it if
your Buzz version differs.

Be clear-eyed about what this means: the load-bearing piece is unspecified and
can change without a spec revision. Publish the specified events too — they are
cheap, they are what a conformant agent looks like, and if 10100 ever changes
they are what will still be right.

### Give the agent an owner even though nothing forces you to

None of the owner-signed events are needed for the agent to work or be
mentioned. Skip them anyway and the UI shows the agent with **"owner
unavailable"** — a bot nobody is accountable for, sitting in a channel with the
ability to act. On a shared relay that is the wrong default.

The attestation is one signature, minted once, reusable forever. Treat it as
part of standing an agent up, not as a finishing touch.

## Order of work

Steps 1–4 are yours. Step 5 needs the **owner's** key and can only be run by
that person on their own machine — the relay rejects any event whose author is
not the connection submitting it (`ingest.rs`: "event pubkey does not match
authenticated identity"), so there is no way to do it for them.

**Before step 1, ask the operator the two questions in
[Decide who can reach it](#decide-who-can-reach-it--ask-do-not-assume)** — who
may mention the agent, and what should wake it in a DM. Both answers set values
in `.env` *and* in the published entry, so deciding them up front costs one
question and deciding them by default costs a debugging session. Do not assume
owner-only just because it is the safe default; an agent nobody else can reach
is often not the agent they asked for.

### 1. Mint the agent an identity and give it relay access

Every agent needs its own keypair. Never reuse a person's key or another
agent's — the key is the unit of revocation.

```bash
docker exec <relay-container> buzz-admin generate-key
```

Capture the secret key straight into your secret store without printing it.

Then choose **one** of two ways in. They are genuinely different, and the second
is what upstream designed for this case:

**Virtual membership via NIP-OA (preferred).** Per `docs/nips/NIP-AA.md`, an
agent presenting a NIP-OA `auth` tag whose owner is an active relay member is
admitted *without being enrolled at all*. The motivation is stated plainly: an
operator otherwise "must also separately enroll every agent that human runs",
and revoking the human leaves their agents behind. With virtual membership,
revoking the owner kills their agents' access on the next connection.

Requires `BUZZ_ALLOW_NIP_OA_AUTH=true` and, on a closed relay,
`BUZZ_REQUIRE_RELAY_MEMBERSHIP=true`. Set `BUZZ_AUTH_TAG` and you are done —
skip `add-member` entirely.

**Explicit membership.** `buzz-admin add-member --pubkey <agent-pubkey-hex>`.
Simpler to reason about, and it costs more than it looks.

It is a second thing to remember to revoke — and, on a relay without
[block/buzz#6098](https://github.com/block/buzz/pull/6098), it also **silently
disables the agent's activity feed**. `check_relay_membership` tests direct
membership first and returns before it ever parses the `auth` tag, so the
agent's owner is never recorded in `users.agent_owner_pubkey`. The relay gates
NIP-AO observer frames on exactly that column and rejects every one of them.
Enrolling the agent is what breaks it; the virtual-membership path records the
owner as a side effect of admitting it. See
[The activity tab](#the-activity-tab-and-the-two-gates-behind-it).

Prefer virtual membership unless something forces your hand.

> countdown-bot's README calls these "standalone bot identity" and
> "owner-attested bot identity". It generates the attestation in-process from
> `BUZZ_OWNER_PRIVATE_KEY`, which means the **owner's private key sits on the
> bot host**. Fine on a laptop; a poor trade for a server. `owner-setup.py`
> instead has the owner sign once on their own machine and ship only the
> resulting signature, which is public by design.

### 2. Build the image — OR go native

**Two deployment shapes. Pick by what the host already is.**

- **Docker** (this section) — the host is a blank VM. Self-contained, isolated,
  the image bundles everything.
- **Native** (`references/native-deploy.md`) — the host **already runs opencode**
  (a workstation, or a box driving opencode through OpenChamber/the TUI). Docker
  would bundle a *second* opencode with a *separate* session store and lose the
  whole point; run `buzz-acp` native beside the existing opencode so the bot
  shares its sessions, config, and model credentials. Build `buzz-acp` **and the
  `buzz` CLI** from source (the sprig binaries are musl and won't run on glibc),
  run as a systemd user service. If that's your host, switch to that reference
  now — the rest of this section is Docker-specific.

For Docker: copy `assets/Dockerfile`, `assets/docker-compose.yml`,
`assets/env.example` and `assets/opencode.json` next to each other and edit them.
Read the comments — each marks a real failure someone has hit.

Base on `ghcr.io/block/buzz-sprig`, pinned by digest. It carries `buzz-acp` and
the `buzz` CLI as symlinks to one binary, so they cannot drift. Building buzz
from source on a small VM takes the best part of an hour and buys nothing.

Add only the tools the agent genuinely needs. Every package is capability handed
to something that acts on chat messages.

**The agent replies via the `buzz` CLI — it must be on the agent's PATH.** The
harness has no built-in post mechanism; it expects the agent to run
`buzz messages send …`. The sprig image already has it. On a native install you
must build and install it too, and put it on the systemd service's PATH — miss
this and the bot connects, thinks, and stays mute. A **virtual member** must also
have `BUZZ_AUTH_TAG` in the agent's environment, or every CLI call returns
`403 relay_membership_required` (same reason the HTTP publisher needs
`x-auth-tag`).

### 3. Point it at the relay — the Host-header trap

A buzz relay routes on the **Host header**. `ws://relay:3000`, the obvious
container-to-container address, answers **404** to the WebSocket upgrade,
because the Host is then `relay:3000` and matches no configured domain. A Host
*with a port* does not match either, so there is no internal shortcut.

- Relay elsewhere → `BUZZ_RELAY_URL=wss://buzz.example.org`, nothing special.
- Relay on the same host → keep the public URL and add
  `extra_hosts: ["buzz.example.org:host-gateway"]`, so traffic crosses the
  bridge to your TLS terminator and stops there. Plain DNS would resolve to the
  public IP, and a cloud VM generally cannot reach its own public IP from inside.

### 4. Choose the model — and know that the obvious way is a trap

`OPENCODE_MODEL` is accepted and **silently ignored**. opencode falls back to
its own default and the only clue is one line of banner output — you get a bill
for a model you never chose. Two things that do work:

- `OPENCODE_CONFIG=/path/to/opencode.json` with `{"model": "provider/model"}`
- `BUZZ_ACP_MODEL`, which the harness applies to each new ACP session

Verify what is genuinely in force, rather than trusting configuration:

```bash
docker exec <agent-container> opencode run "say ok"
```

The banner line (`> build · moonshotai/kimi-k3`) is the model it really used.

Bring the agent up with `docker compose up -d`. It should log
`connected to relay`, `discovered N channel(s)` and `presence set to online`.

### 5. Hand the owner their part

Two commands, run by the workspace owner on their own machine. Their key is
typed at a hidden prompt, never an argument, never printed.

```bash
python3 scripts/owner-setup.py --selftest
```

```bash
python3 scripts/owner-setup.py \
  --relay https://buzz.example.org \
  --agent <agent-pubkey-hex> \
  --name my-agent \
  --channel general=<uuid> --channel ops=<uuid>
```

That mints the NIP-OA `auth` tag (printed — it is public), seats the agent as
`role=bot` in each channel, and publishes the optional kind 30177 record. Put
the printed tag in the agent's `BUZZ_AUTH_TAG` — it doubles as the relay
credential under NIP-AA.

`--channel` seats the agent as `role=bot`, which is presentation rather than
function — do it for channels you want it to look like a bot in.

⚠ **Seat the agent *after* publishing its profile, not before.** The obvious
order — run this once with `--channel`, then publish the profile — puts the
kind 9000 membership event on the relay while the agent still has no kind 0, so
the "added by" line renders as a raw `b226101c…9f59` for everyone in the
channel. Clicking the agent resolves the name (the client fetches the profile on
demand) and a refresh fixes the line, but until then a freshly added agent looks
anonymous to every member.

To avoid it, split this into two runs:

```bash
# 1. mint the attestation only — no --channel
python3 scripts/owner-setup.py --relay ... --agent <hex> --name my-agent
# 2. publish the agent's own profile with the tag from step 1
BUZZ_AGENT_AUTHTAG='["auth",...]' ... python3 scripts/agent-profile.py
# 3. NOW seat it, with a name already on the relay
python3 scripts/owner-setup.py --relay ... --agent <hex> --name my-agent \
  --channel general=<uuid>
```

Kind 0 and kind 10100 are replaceable and the attestation signs the agent's key
rather than an event, so the repeated run is harmless.

**Channel membership is separate, and `buzz-acp` will not do it for you.**
countdown-bot self-adds from its own code; the harness does not. An open channel
the agent can join itself:

```bash
docker exec -e BUZZ_RELAY_URL=https://buzz.example.org <agent> buzz channels list
docker exec -e BUZZ_RELAY_URL=https://buzz.example.org <agent> buzz channels join --channel <uuid>
```

A private channel returns `restricted: channel is private` — an existing member
must add it. Either way the directory entry must be republished afterwards, or
it joins silently and nobody can mention it there.

Then, as the **agent**, publish its profile:

```bash
BUZZ_AGENT_SECKEY=... BUZZ_RELAY=https://buzz.example.org \
BUZZ_AGENT_NAME=my-agent BUZZ_AGENT_OWNER=<owner-hex> \
BUZZ_AGENT_CHANNELS=auto \
BUZZ_AGENT_AUTHTAG='["auth",...]' \
python3 scripts/agent-profile.py
```

`BUZZ_AGENT_ADD_POLICY` (`anyone` by default, or `owner_only` / `nobody`) sets
who may add the agent to a channel. The relay **requires** this field in the
entry — `handle_agent_profile` errors without it and logs `Side effect failed:
kind:10100 missing channel_add_policy field` — but still answers the publisher
`accepted: true`, so omitting it looks like a clean publish while the policy is
never applied. Republished every time, so this is also how you change it.
`owner_only` stops anyone but you seating the agent somewhere it will then read.

`BUZZ_AGENT_CHANNELS=auto` asks the relay which channels the agent is actually
in, reading its own signed rosters (kind 39002) rather than trusting a list
here. Prefer it. A hand-written `'general=<uuid>,ops=<uuid>'` still works, but
it is stale the moment anyone adds the agent to a channel without redeploying,
and the only symptom is that nobody can mention it there. If discovery fails,
the script refuses to publish rather than shipping a partial entry that would
un-mention the agent everywhere it omits.

Run that on **every deploy**. Kind 10100 is replaceable, and
`buzz channels set-add-policy` publishes a 10100 containing only
`{"channel_add_policy": ...}` — so that one command wipes the profile and
un-mentions the agent. Republishing makes the damage self-repairing.

Restart Buzz Desktop afterwards; it caches the directory.

> Publishing 10100 out-of-band like this is a workaround for `buzz-acp` never
> writing it — the one event that decides mentionability is the one the harness
> does not publish, even though it already subscribes to membership
> notifications and knows its own channel set.
> [block/buzz#6097](https://github.com/block/buzz/pull/6097) has the harness
> publish and refresh its own entry. If your `buzz-acp` carries that, this step
> and its every-deploy discipline become unnecessary; without it, keep both.

## Channels: joining versus being seated

Two different things, and conflating them wastes hours.

**Membership** lets the agent see the channel. `NOSTR.md`: for kind 9000,
"Open: any user… Private: owner/admin only. Self-add bypasses agent policy but
not private-channel auth." So in an **open** channel the agent can join itself:

```bash
docker exec -e BUZZ_RELAY_URL=https://buzz.example.org <agent> buzz channels list
docker exec -e BUZZ_RELAY_URL=https://buzz.example.org <agent> buzz channels join --channel <uuid>
```

In a **private** channel that returns `restricted: channel is private`. Only an
existing member can add it — no amount of relay admin substitutes.

**Seating as `role=bot`** is separate, always requires owner/admin, and is what
puts the agent in the members list as a bot. Upstream's `examples/countdown-bot`
is the reference: it publishes a profile and then "best-effort publishes a
NIP-29 `kind:9000` self-add with `role=bot`. That channel membership is what
makes the bot show up in the members list and in Buzz's mention autocomplete."

The CLI's `buzz channels add-member --role` is broken upstream — it builds the
event without the required `p` tag and fails `invalid: missing p tag`. Use
`owner-setup.py`.

**Re-run `agent-profile.py` after any channel membership change,** or the agent
will be a member nobody can mention. Nothing republishes the entry on its own:
`buzz-acp` subscribes to membership notifications and knows the instant its
channel set changes, but does not write kind 10100 (that is #6097). With
`BUZZ_AGENT_CHANNELS=auto` the re-run needs no other edit.

## Decide who can reach it — ask, do not assume

There are two independent questions here and the defaults answer neither of them
well. **Ask the operator both before publishing anything.** Each answer sets two
places at once, and getting them out of step is the most common silent failure in
this document.

### Question 1 — who may mention it in a channel?

> *"Should only you be able to mention this agent, specific named people, or
> anyone who is in a channel with it?"*

| Their answer | 10100 `respond_to` | 10100 allowlist | `BUZZ_ACP_RESPOND_TO` | harness allowlist |
|---|---|---|---|---|
| **Only me** (default) | `allowlist` | owner | `allowlist` | owner |
| **These specific people** | `allowlist` | owner + theirs | `allowlist` | owner + theirs |
| **Anyone in its channels** | `anyone` | — | `anyone` | — |

Set the published half with `BUZZ_AGENT_RESPOND_TO` / `BUZZ_AGENT_ALLOWLIST` and
the harness half in `.env`. Both columns must agree — see the two-list section.

Note the last row is **channel-scoped, not relay-wide**: the client also requires
the viewer to share one of the agent's channels, so "anyone" means anyone who is
already in a room with it. That makes channel membership the boundary, which is
usually what people mean, and it removes the need to collect anyone's pubkey.

Publishing `owner-only` in the entry is not an option — the client tests for
exactly `allowlist` or `anyone`, so anything else hides the agent from everyone
including the owner. "Only me" is expressed as an allowlist of one.

### Question 2 — what should wake it in a DM?

> *"In a DM, should it answer every message, or only when you @mention it?"*

**Authorisation is not a choice here.** `author_allowed()` short-circuits on
`is_dm` and returns `is_owner_or_sibling` for every mode except `nobody`, so no
setting lets a non-owner DM the agent — not even `anyone`, and not by adding
someone to an allowlist. The only DM-adjacent value is `nobody`, which turns the
agent off entirely rather than just its DMs. Siblings — other agents with the
same owner — are admitted alongside the owner.

What *is* configurable is which events wake it:

| Their answer | `BUZZ_ACP_SUBSCRIBE` | What happens |
|---|---|---|
| @mentions in channels (default) | `mentions` | `require_mention` on every channel. **DMs still work** — see below |
| Everything, everywhere | `all` | it also chimes into every channel message uninvited |
| Per-channel control | `config` + rules file | the only way to mix the two |

**`mentions` does not break DMs, despite what you might expect** — and this
document said otherwise until it was tested against a live relay.
`require_mention` is a **`p`-tag check, not a text check**: `filter.rs` requires
"a `p` tag matching `agent_pubkey_hex`", and Buzz Desktop's DM composer p-tags
the other participant on every message. So a plain DM with no `@` in it carries
the tag anyway and is delivered. Verified: `hello, are you there?` sent to an
agent on default `mentions` was dispatched and answered.

The caveat is that this is a **client convention, not a protocol guarantee**. A
client that does not p-tag the recipient would be filtered out, and then a rules
file is the fix. Test it rather than assuming, in either direction.

So reach for `config` when you want something `mentions` and `all` genuinely
cannot express — most often "answer everything in this one channel, @mentions
everywhere else":

```toml
# buzz-acp.toml — first match wins at dispatch, merged per channel for the
# relay-side filter.
[[rules]]
name = "dms"
channels = ["<dm-channel-uuid>", "..."]   # UUIDs only; there is no "type = dm"
require_mention = false
prompt_tag = "dm"

[[rules]]
name = "mentions"
channels = "all"
require_mention = true
prompt_tag = "@mention"
```

```bash
BUZZ_ACP_SUBSCRIBE=config
BUZZ_ACP_CONFIG=/etc/buzz-agent/buzz-acp.toml
```

⚠ Channel scope is **UUIDs only** — there is no matcher for "is a DM". So a new
DM channel, opened the first time someone DMs the agent, is covered only by the
catch-all `mentions` rule until you add its UUID and redeploy. Its first DM will
be ignored unless it carries an @mention.

### "So how do I let a teammate DM it?" — you don't, you give them a room

This is the next question everyone asks, and the answer is not a setting. There
is no flag, env var or rule that admits a non-owner in a DM: `author_allowed` is
the only gate, nothing bypasses it, and `is_dm_channel` fails *closed* when it
cannot resolve the channel type. Treat it as deliberate — otherwise any relay
member could privately drive someone else's agent, running
`permission_mode=bypassPermissions`, with nothing visible in a shared channel.

What gives you the same experience is a **private channel containing just that
person and the agent**. The gate keys on `channel_type`, and a private channel
is `stream`, not `dm` — so `allowlist`/`anyone` apply normally there. Add a rule
with `require_mention = false` for its UUID and it answers every message without
an `@`:

```toml
[[rules]]
name = "direct-with-<person>"
channels = ["<that-channel-uuid>"]
require_mention = false
prompt_tag = "dm"
```

`owner-setup.py --create-channel <name>` already produces exactly this shape — a
private `stream` channel you own, with the agent seated in it. It reads as a DM
to both of them, the authorisation is yours to set, and unlike a real DM it is
auditable.

## Letting someone else mention it — two lists, and both must change

By default only the owner can. Widening that means updating **two** lists, and
changing one without the other fails silently in a way that wastes an afternoon.

| List | Where | Decides |
|---|---|---|
| `BUZZ_ACP_RESPOND_TO_ALLOWLIST` | the agent's `.env` | whether the harness **answers** them |
| `respond_to_allowlist` in kind 10100 | the published entry | whether their client **offers** it |

Set `BUZZ_AGENT_ALLOWLIST` to the same comma-separated set when you publish, and
restart the agent with the matching env:

```bash
BUZZ_AGENT_ALLOWLIST='<teammate-hex>,<teammate-hex>' ... python3 scripts/agent-profile.py
```

The owner is always included, so list only the additions.

**Update the harness gate alone and the agent becomes an agent that would
happily answer someone who cannot see it.** `relayAgentIsSharedWithUser` checks
the published list against the pubkey of the person *typing*, so the agent is
absent from their autocomplete. They get no error. A mention they type by hand
goes out with **no `p` tag** and routes nowhere, so the agent never sees it
either. From their side it looks broken; from yours it looks fine, because you
are on both lists.

Update the published entry alone and it is the mirror image: they can see and
mention it, and the harness discards every message they send.

### Or drop the lists: `anyone`, which is channel-scoped

If you want everyone in a channel to be able to mention the agent without
collecting anyone's pubkey, set both gates to `anyone` —
`BUZZ_AGENT_RESPOND_TO=anyone` when publishing, `BUZZ_ACP_RESPOND_TO=anyone` in
the env — and leave the allowlists empty.

This is **not** relay-wide, in either half:

- The client still requires the viewer to share a channel with the agent:
  `relayAgentIsSharedWithUser` falls through to
  `channelIds.some(id => sharedChannelIds.has(id))`. Someone on the relay who is
  in none of its channels never sees it.
- **DMs stay owner-only.** `author_allowed` short-circuits on `is_dm` and
  returns `is_owner_or_sibling` for every mode except `Nobody`, so `anyone` does
  not open DMs to anybody — only the owner and sibling agents with the same
  owner can DM it. The resolution fails *closed* to DM if the channel type
  cannot be determined.

So the boundary becomes **channel membership** rather than a pubkey list, which
is usually what you actually wanted. Judge it by the channel: in a private
channel you control, `anyone` is the right default and saves the two-list
problem entirely. In an open channel, it hands everyone who joins the ability to
make an agent act, and the harness runs `permission_mode=bypassPermissions` —
so scope that by what the agent can reach, not by who can talk to it.

## The activity tab, and the two gates behind it

Buzz Desktop shows a per-agent **ACP activity** tab. For a hand-provisioned
agent it is empty, and it stays empty through every obvious fix, because two
independent things must both be true and neither is on by default.

**1. The harness must publish.** NIP-AO telemetry is opt-in:

```
--relay-observer    Publish encrypted ACP observer frames over the relay
                    [env: BUZZ_ACP_RELAY_OBSERVER=]
```

Off unless you set it. The table above lists NIP-AO as "handled by buzz-acp" in
the sense that you do not implement it — not in the sense that it runs.

**2. The relay must accept.** It gates kind 24200 on `users.agent_owner_pubkey`
and rejects every frame from an agent with no owner recorded there:

```
restricted: observer frame is not authorized for this agent owner
```

An agent admitted by NIP-OA virtual membership gets that column populated as a
side effect of being admitted. An agent enrolled with `add-member` does not:
`check_relay_membership` matches direct membership first and returns before the
`auth` tag is read, so the proof it presented is discarded on every connection.
Fixed upstream in [block/buzz#6098](https://github.com/block/buzz/pull/6098);
until that is in your relay, an enrolled agent has no working activity feed.

**The failure is silent at every layer.** The relay logs nothing — it only
increments `buzz_events_rejected_total{reason="auth"}`. buzz-acp never surfaces
the `OK=false`. So the agent logs `relay observer enabled`, reports a resolved
owner, and looks perfectly healthy while every frame it sends is dropped.

Two more properties that read as faults but are not:

- **It is live, not a log.** Kind 24200 is in the ephemeral range and NIP-01
  says relays MUST NOT persist those events. Nothing replays. An idle agent
  correctly shows nothing. To see anything, open the tab *first*, then prompt
  the agent and watch.
- **Only the owner can read it.** Frames are NIP-44 encrypted with
  `(agent_privkey, owner_pubkey)` and `p`-tagged to the owner. People who may
  mention the agent still cannot watch it work. The channel is bidirectional —
  it also carries `control` frames owner → agent.

If the tab is empty on a working agent, these two only prove it is *sending*:

```bash
docker exec <agent-container> sh -c 'env | grep OBSERVER'   # expect =true
docker logs <agent-container> | grep -i observer            # "relay observer enabled"
```

The check that separates the two failures is whether the relay will accept:

```bash
docker exec <relay-postgres> psql -U buzz -d buzz -tAc \
  "SELECT coalesce(encode(agent_owner_pubkey,'hex'),'NO OWNER — frames rejected')
   FROM users WHERE encode(pubkey,'hex')='<agent-pubkey-hex>'"
```

`NO OWNER` means gate 2. There is no `buzz-admin` command for it; the mapping is
written by the relay when it admits an agent that proves an owner. Reconnecting
the agent on a relay carrying #6098 writes it. Otherwise it is one `UPDATE`,
matching what `buzz-db`'s `set_agent_owner` does (`IS NULL`-guarded, since the
column is first-write-wins).

## Verifying, in the order that isolates fastest

```bash
docker logs <agent-container> 2>&1 | tail -30
```

Look for `connected to relay`, `agent owner:`, `discovered N channel(s)`.
`no agent owner configured` means DMs are being dropped.

Then, if the agent seems deaf, turn logs up **before** theorising:

```bash
# RUST_LOG=buzz_acp=debug, then restart, then send it a message
docker logs -f <agent-container> 2>&1 | grep -E 'agent_claimed|agent_returned|dropping'
```

`agent_claimed` → `agent_returned outcome="ok"` means the pipeline is healthy.
A `dropping` line names the gate that rejected the message. Silence means the
event never arrived.

**At info level, "received and discarded" and "never arrived" look identical.**
Both produce no output. Do not infer non-delivery from quiet logs — that mistake
costs an hour every time.

## Troubleshooting

| Symptom | Cause |
|---|---|
| WebSocket 404 | Host header — see step 3 |
| `discovered 0 channel(s) — agent will sit idle` | Relay member but not a channel member |
| `restricted: channel is private` | Only an existing member can add it |
| Answers channels, ignores DMs | `BUZZ_ACP_AGENT_OWNER` unset. *Not* the subscribe mode — `mentions` delivers DMs fine, because the client p-tags the recipient |
| A teammate's DMs are ignored, their @mentions work | Correct and not configurable: DMs are owner-or-sibling in every mode. Give them a private channel instead — see above |
| Not in `@` autocomplete | Missing kind 10100 with `channel_ids`, or not seated `role=bot` |
| One person cannot mention it, everyone else can | Usually their client, not your config — see below |
| Absent from `@` autocomplete **in a DM**, fine in channels | By design, and not fixable from your side — see below |
| A teammate cannot mention it, and never could | They are on the harness gate but not in the published `respond_to_allowlist` |
| Not in the Agents panel | Missing kind 30177 |
| Shows as a raw pubkey in the channel, right name when clicked | Seated before its kind 0 existed — publish the profile before `--channel`, see step 5 |
| No "managed by" badge | Missing NIP-OA `auth` tag, or a kind 0 published without it |
| ACP activity tab empty | `BUZZ_ACP_RELAY_OBSERVER` unset — or set, and the relay has no owner recorded for the agent |
| Activity tab empty only for a teammate | Correct: frames are encrypted to the owner alone |
| Wrong model in the banner | `OPENCODE_MODEL` ignored — use a config file, or `BUZZ_ACP_MODEL` |
| Connects and thinks but never posts | no `buzz` CLI on the agent's PATH |
| CLI `403 relay_membership_required` | virtual member without `BUZZ_AUTH_TAG` in the env |
| `opencode run` auto-rejects a `buzz` post | `opencode.json` "ask" gate; the harness bypasses it, `opencode run` does not |
| `__cxa_guard_acquire: symbol not found` | Alpine lacks `libstdc++`/`libgcc` |
| Ansible: `DEFAULT_LOCAL_TMP: Permission denied` | Root-owned `/home/agent` dotfiles |

### Missing from `@` autocomplete in a DM

Expected, and no amount of republishing changes it. `useMentions` only treats a
channel as mentionable when `isAgentMentionChannelType` says so:

```js
return type === "stream" || type === "forum";
```

A DM is `"dm"`, so the scope falls back to `{ type: "managed-only" }`, and
`getMentionableAgentPubkeys` returns `false` for every relay agent under that
scope regardless of `channel_ids`, `respond_to` or the allowlist.

**It does not matter.** A DM has one other participant, so there is nothing to
disambiguate, and the message reaches the agent anyway — the client p-tags the
recipient, which is the same mechanism that makes plain DMs work under
`mentions` mode. Type nothing special and it will answer.

Worth knowing only because the behaviour is inconsistent: an agent the desktop
*manages locally* still appears in DM autocomplete, because `managed-only`
admits it. So the same UI offers a laptop-run agent and hides a server-run one.
Cosmetic — do not go hunting for a config difference, there isn't one.

### When only one person cannot mention it

This one is worth its own entry because it is indistinguishable from a
misconfiguration and will send you through the relay looking for a per-user
gate that does not exist.

Buzz Desktop builds its mentionable set as **managed agents ∪ relay agents**,
and humans come from channel members — three independent paths. So ask the
person one question: **can they still `@` a human, and a locally managed
agent?** If both work and only the server-side agent does not, their relay-agent
half is empty and nothing on your side is wrong. Kind 10100 has no event-driven
refresh in the desktop — a poll is the only path that repopulates it — so the
list can go blank and stay blank. Restarting Buzz Desktop rebuilds it.

The tell in the relay's own data is exact: their message arrives with **no `p`
tag at all**, while the same person's earlier messages carry the agent's pubkey.
They typed the mention, their client did not offer the agent, and it went out as
plain text that routes nowhere. Neither side shows an error.

```sql
-- did their client actually tag anything? (relay Postgres)
SELECT created_at,
       (SELECT string_agg(left(t->>1,8), ',') FROM jsonb_array_elements(tags) t
         WHERE t->>0 = 'p') AS p_tags,
       left(content, 50)
FROM events WHERE kind = 9 AND encode(pubkey,'hex') = '<their pubkey>'
  AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 10;
```

An empty `p_tags` on a message that visibly starts with `@agent` is the whole
diagnosis. Before concluding it is their client, confirm the entry really does
list the channel and their pubkey — those are yours to get wrong:

```sql
SELECT content FROM events WHERE kind = 10100
  AND encode(pubkey,'hex') = '<agent pubkey>' AND deleted_at IS NULL;
```

⚠ When querying membership directly, filter `removed_at IS NULL`. A removed
member still has its row, so an unfiltered `channel_members` query reports
people and agents as present who were removed hours ago.

More depth, including the exact upstream source references behind each of these:
`references/internals.md`. For running native (systemd, shared opencode, build
from source): `references/native-deploy.md`.

## Retiring an agent — order matters

Do this **before** destroying the key, and archive rather than delete.

```bash
python3 scripts/owner-setup.py --relay https://buzz.example.org \
  --agent <agent-pubkey-hex> --archive --reason retired
```

Then stop it and remove the key material:

```bash
docker compose down -v          # in the agent's directory
buzz-admin remove-member --pubkey <agent>   # only if explicitly enrolled
shred -u key.txt .env
```

**Archive, do not delete.** NIP-IA exists for precisely this case — its
motivation names "agents created from temporary worktrees [that] continue to
appear in member pickers long after they are useful". The relay publishes a
signed kind 13535 archive snapshot, and clients "SHOULD hide archived
identities from active-member lists, mention autocomplete, invite dialogs,
agent pickers" while "MUST NOT hide or rewrite historical events solely because
their author is archived."

So after archiving, **the agent's kind 0 and 10100 events are still in the
store and still returned by a raw query. That is correct.** Do not go deleting
rows to "finish the job" — you would be destroying history the spec says to
keep. Verify with the archive snapshot instead:

```bash
buzz agents archived      # or query kind 13535 from the relay identity
```

Two failure modes worth knowing:

- **Key destroyed first.** Then nothing can sign as the agent, and the only
  routes left are an owner attestation (if you can still mint one) or relay
  admin. `buzz-admin deletions` is *not* an escape hatch — it has no target
  selector and operates on the whole community.
- **Missing the `["-"]` tag.** A kind 9035 needs exactly one `p` tag and
  exactly one NIP-70 `-` tag; without the latter the relay rejects it with
  "request must include exactly one NIP-70 protected event tag".

## Turning an existing opencode session into a buzz agent

You cannot attach one. `buzz-acp` always calls `session/new` — its
`--session-title` flag is documented as "passed out-of-band in `session/new`
`_meta`" — and there is no flag taking a session id. opencode's ACP server does
advertise `loadSession` and session `resume`, so the protocol allows it, but the
harness never exposes it.

What works instead:

- **Share the workspace.** Point the agent at the same directory and it picks up
  the same `AGENTS.md`, config and files. That is most of what "continue my
  session" usually means.
- **Share opencode's state.** Mount the same `/home/agent` volume and its
  history is present on disk, even though the harness starts new sessions.
- **Hand over context explicitly.** Summarise the session into a file the agent
  reads, or into its NIP-AE memory (`buzz mem`), which the harness injects into
  prompts automatically.

## Other harnesses

Anything speaking ACP over stdio works — change two variables:

| Agent | `BUZZ_ACP_AGENT_COMMAND` | `BUZZ_ACP_AGENT_ARGS` | Needs |
|---|---|---|---|
| opencode | `opencode` | `acp` | native, no adapter |
| goose | `goose` | `acp` | native |
| Claude Code | `claude-agent-acp` | – | `npm i -g @agentclientprotocol/claude-agent-acp` |
| Codex | `codex-acp` | – | `npm i -g @agentclientprotocol/codex-acp` |

opencode and goose are the least trouble because nothing sits between the
harness and the agent.

## Security worth stating out loud

The author gate decides **who may prompt** the agent — not what it may read.
Once it is working on a task it may read a web page, a forum thread or a diff,
and text in any of those can try to steer it from inside an already-authorised
session. `permission_mode=bypassPermissions` is the harness default, so there is
no per-command approval to catch it.

So the mount and the credentials are the real boundary. Mount only what the
agent needs, and prefer short-lived, narrowly-scoped credentials. On AWS, an
instance-profile role assumed by the container beats any long-lived key: nothing
is written to disk and the role can be deleted instantly. Put a hard deny on
anything irreversible — deleting backups, changing IAM, terminating instances —
because an explicit deny survives every later widening of permissions.
