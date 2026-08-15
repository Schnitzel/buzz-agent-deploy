# buzz-agent-deploy

Run a [buzz](https://github.com/block/buzz) agent on a **server** instead of a
laptop — [opencode](https://opencode.ai) (or goose, Codex, Claude Code) under
the `buzz-acp` harness, in Docker — and make it actually visible and
`@`-mentionable in Buzz Desktop.

Upstream supports this: `docs/remote-agents.md` says a script exporting
`BUZZ_PRIVATE_KEY` and `BUZZ_RELAY_URL` and exec'ing the harness "is a
conforming launcher at this layer — today, with no code change." What upstream
does *not* spell out is everything else an agent needs before a human can use
it, and **every one of those things fails silently**.

This repo is the missing half: a checklist, the Docker pieces, and small
dependency-free scripts for the parts that need a Nostr signature.

## Why this exists

An agent created inside Buzz Desktop gets five things done for it invisibly. One
provisioned anywhere else gets none of them, and no error tells you which is
missing. Symptoms that all mean "a different one of the five":

| What you see | What is actually missing |
|---|---|
| Answers in channels, ignores every DM | `BUZZ_ACP_AGENT_OWNER` — DMs gate on owner, not the allowlist |
| Never appears in `@` autocomplete | kind 10100 directory entry |
| Absent from the Agents panel | kind 30177 instance record |
| Shows "owner unavailable" | NIP-OA owner attestation |
| `discovered 0 channel(s) — agent will sit idle` | relay member, but not a channel member |
| `404` on the relay WebSocket | the relay routes on the Host header |

## Findings you will not find in the docs

These came out of building two agents against a live relay and diffing them
against one created by the desktop. Several contradict the documentation.

**Kind 10100 is what makes an agent mentionable, and it is in no NIP.** Buzz
Desktop offers an agent in autocomplete if it manages the agent locally — which
a server agent can never be — or if the agent is in the relay-agent directory
passing `relayAgentCanRespondInChannel`. That check reads kind 10100. Proven by
controlled comparison:

| | agent A | agent B |
|---|---|---|
| kind 10100 | ✗ | **✓** |
| `role=bot` seating | ✓ | ✗ |
| kind 30177 | ✓ | ✗ |
| NIP-OA attestation | ✓ | ✗ |
| **mentionable** | **no** | **yes** |

This contradicts `examples/countdown-bot`'s README, which credits the `role=bot`
self-add. That claim looks stale for current Buzz Desktop.

**A DM is gated on the agent's owner, and the allowlist does not substitute.**
With no `BUZZ_ACP_AGENT_OWNER`, an allowlisted person's channel mentions work
while every DM from that same person is dropped. The only evidence is one DEBUG
line. At default log level, "received and discarded" and "never arrived" are
indistinguishable — both produce silence.

**`OPENCODE_MODEL` is accepted and silently ignored.** opencode falls back to
its own default; the only clue is one line of banner output, and a bill for a
model you did not choose. Use a config file via `OPENCODE_CONFIG`.

**HTTP and WebSocket carry the NIP-AA credential differently.** An agent on
virtual membership connects fine over WebSocket (credential in the NIP-42 AUTH
event) while every HTTP publish returns `403 relay_membership_required` — the
bridge wants an `x-auth-tag` header. Easy to misread as a broken key.

**Kind 0 and 10100 are replaceable, so partial writes destroy state.**
`buzz channels set-add-policy` publishes a 10100 containing only
`{"channel_add_policy": ...}` — wiping the directory entry and silently
un-mentioning the agent. Republish the full profile on every deploy.

**Retire with NIP-IA, do not delete.** Archive the identity *before* destroying
its key — afterwards nothing can sign as it. Archiving hides it from pickers and
autocomplete while preserving history, which is the designed behaviour; deleting
its events destroys history the spec says to keep.

## Layout

```
SKILL.md                    the procedure, as a Claude Code skill
references/internals.md     why each rule exists, with upstream source refs
assets/Dockerfile           sprig base + opencode musl
assets/docker-compose.yml   Host-header wiring, resource caps
assets/env.example          every variable, with the silent failures marked
scripts/nostr.py            BIP-340 + NIP-01 + NIP-98, stdlib only
scripts/owner-setup.py      owner-signed: attestation, 30177, role=bot, archive
scripts/agent-profile.py    agent-signed: kind 0 profile, kind 10100 directory
```

The scripts are plain Python 3 with **no dependencies**, usable without Claude
Code. BIP-340 is implemented inline rather than pulled from a library, because
asking someone to `pip install` a crypto package before pasting in their nsec is
the worse trade. Verify it before trusting it:

```bash
python3 scripts/nostr.py --selftest      # official BIP-340 vectors + round trip
```

Secret keys are always read from a hidden prompt or the environment — never an
argument, so they stay out of shell history — and are never printed.

## Quickstart

```bash
# 1. mint the agent an identity
docker exec <relay> buzz-admin generate-key

# 2. owner authorises it (their machine, their key)
python3 scripts/owner-setup.py --relay https://buzz.example.org \
  --agent <agent-pubkey> --name my-agent \
  --model openrouter/moonshotai/kimi-k3 --provider openrouter \
  --channel general=<uuid>

# 3. deploy — copy assets/, fill in .env with the printed BUZZ_AUTH_TAG
docker compose up -d

# 4. publish the agent's own profile
BUZZ_AGENT_SECKEY=... BUZZ_RELAY=... BUZZ_AGENT_NAME=my-agent \
BUZZ_AGENT_OWNER=... BUZZ_AGENT_CHANNELS='general=<uuid>' \
BUZZ_AGENT_AUTHTAG='["auth",...]' python3 scripts/agent-profile.py
```

With the attestation in place the agent needs no explicit relay enrolment at
all — [NIP-AA](https://github.com/block/buzz/blob/main/docs/nips/NIP-AA.md)
virtual membership admits it because its owner is a member, and revoking the
owner cuts off their agents automatically.

Retiring it, in this order:

```bash
python3 scripts/owner-setup.py --relay ... --agent <pubkey> --archive
docker compose down -v && shred -u .env key.txt
```

## Using it as a Claude Code skill

Clone into your skills directory and it loads automatically:

```bash
git clone https://github.com/Schnitzel/buzz-agent-deploy.git \
  ~/.claude/skills/buzz-agent-deploy
```

## Caveats

Tested against buzz as of August 2026, with opencode 1.18.18 and the
`ghcr.io/block/buzz-sprig` image. The load-bearing piece — kind 10100 — is
unspecified and can change without a NIP revision, so re-run the comparison if
your Buzz version differs. The specified events are published too: they are
cheap, they are what a conformant agent looks like, and they are what will still
be right if 10100 moves.

## Licence

MIT. See [LICENSE](LICENSE).
