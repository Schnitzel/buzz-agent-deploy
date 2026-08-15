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
  autocomplete or the Agents panel, shows no "managed by" badge, sits idle
  reporting "discovered 0 channel(s)", or 404s on the relay WebSocket. Reach
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
| Turn metrics / observability | NIP-AM, NIP-AO | agent | handled by buzz-acp |
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
Simpler to reason about, but it is a second thing to remember to revoke.

> countdown-bot's README calls these "standalone bot identity" and
> "owner-attested bot identity". It generates the attestation in-process from
> `BUZZ_OWNER_PRIVATE_KEY`, which means the **owner's private key sits on the
> bot host**. Fine on a laptop; a poor trade for a server. `owner-setup.py`
> instead has the owner sign once on their own machine and ship only the
> resulting signature, which is public by design.

### 2. Build the image

Copy `assets/Dockerfile`, `assets/docker-compose.yml`, `assets/env.example` and
`assets/opencode.json` next to each other and edit them. Read the comments —
each marks a real failure someone has hit.

Base on `ghcr.io/block/buzz-sprig`, pinned by digest. It carries `buzz-acp` and
the `buzz` CLI as symlinks to one binary, so they cannot drift. Building buzz
from source on a small VM takes the best part of an hour and buys nothing.

Add only the tools the agent genuinely needs. Every package is capability handed
to something that acts on chat messages.

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

**Channel membership is separate, and `buzz-acp` will not do it for you.**
countdown-bot self-adds from its own code; the harness does not. An open channel
the agent can join itself:

```bash
docker exec -e BUZZ_RELAY_URL=https://buzz.example.org <agent> buzz channels list
docker exec -e BUZZ_RELAY_URL=https://buzz.example.org <agent> buzz channels join --channel <uuid>
```

A private channel returns `restricted: channel is private` — an existing member
must add it. Either way, put the channel in `BUZZ_AGENT_CHANNELS` and re-run
`agent-profile.py`, or it joins silently and nobody can mention it.

Then, as the **agent**, publish its profile:

```bash
BUZZ_AGENT_SECKEY=... BUZZ_RELAY=https://buzz.example.org \
BUZZ_AGENT_NAME=my-agent BUZZ_AGENT_OWNER=<owner-hex> \
BUZZ_AGENT_CHANNELS='general=<uuid>,ops=<uuid>' \
BUZZ_AGENT_AUTHTAG='["auth",...]' \
python3 scripts/agent-profile.py
```

Run that on **every deploy**. Kind 10100 is replaceable, and
`buzz channels set-add-policy` publishes a 10100 containing only
`{"channel_add_policy": ...}` — so that one command wipes the profile and
un-mentions the agent. Republishing makes the damage self-repairing.

Restart Buzz Desktop afterwards; it caches the directory.

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

**Add every new channel to `BUZZ_AGENT_CHANNELS` and re-run `agent-profile.py`,**
or the agent will be a member nobody can mention.

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
| Answers channels, ignores DMs | `BUZZ_ACP_AGENT_OWNER` unset |
| Not in `@` autocomplete | Missing kind 10100 with `channel_ids`, or not seated `role=bot` |
| Not in the Agents panel | Missing kind 30177 |
| No "managed by" badge | Missing NIP-OA `auth` tag, or a kind 0 published without it |
| Wrong model in the banner | `OPENCODE_MODEL` ignored — use a config file |
| `__cxa_guard_acquire: symbol not found` | Alpine lacks `libstdc++`/`libgcc` |
| Ansible: `DEFAULT_LOCAL_TMP: Permission denied` | Root-owned `/home/agent` dotfiles |

More depth, including the exact upstream source references behind each of these:
`references/internals.md`.

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
