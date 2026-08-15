# Native deploy: buzz-acp beside an existing opencode

When to prefer this over the Docker path in SKILL.md: the host **already runs
opencode natively** — a workstation, or a dedicated box driving opencode through
a web UI (OpenChamber) or the TUI. Docker would bundle a *second* opencode with
a *separate* session store, throwing away the whole reason to run on that box:
the sessions, config, and model credentials are already there. Native makes the
bot a peer of the existing opencode over one shared store.

This is how the 256 red-team bot runs on a dedicated VPS.

## The shape

```
systemd --user
  ├── openchamber.service   (optional — the human's web UI → opencode serve)
  └── buzz-acp.service      → buzz-acp → spawns `opencode acp`
                                          │
                                          ├── shared ~/.local/share/opencode/opencode.db
                                          ├── shared ~/.config/opencode (model auth)
                                          └── the `buzz` CLI on PATH (to reply)
```

Every opencode process on the box — the web UI's `opencode serve`, the bot's
`opencode acp`, any TUI — reads one SQLite session db. So the bot can
`opencode export <id>` any existing session and read it in full. No import.

Two opencode processes on one SQLite db is fine for normal use (WAL: concurrent
readers, single writer). Under simultaneous heavy writes they can briefly
contend; if that ever bites, give the bot its own store and import sessions
across.

## Build buzz-acp and the buzz CLI from source

The `ghcr.io/block/buzz-sprig` binaries are musl-dynamic and will not run on a
glibc host (Ubuntu/Debian/etc.). Build both from source — fast on a real box,
and you need **both**: `buzz-acp` (the harness) and `buzz` (the CLI the agent
replies with).

```bash
# deps (Debian/Ubuntu)
sudo apt-get install -y protobuf-compiler libssl-dev pkg-config cmake build-essential git
# rust — rustup is cleanest; a one-off toolchain in /tmp is fine since only the
# built binaries need to persist, not cargo.

git clone --depth 1 https://github.com/block/buzz.git ~/buzz-src
cd ~/buzz-src
cargo build --release -p buzz-acp -p buzz-cli     # deps shared; ~a few minutes

install -Dm755 target/release/buzz-acp ~/.local/bin/buzz-acp
install -Dm755 target/release/buzz    ~/.local/bin/buzz
```

## The `buzz` CLI is not optional

The harness has no built-in way to post. It expects the agent to reply by
running `buzz messages send …` from its shell tool. So:

- `buzz` must be on the **agent's** PATH — i.e. in the systemd unit's
  `Environment=PATH=`, because opencode inherits the service env.
- The agent's opencode subprocess inherits `BUZZ_PRIVATE_KEY`, `BUZZ_RELAY_URL`
  and `BUZZ_AUTH_TAG` from the service, which is what the CLI authenticates with.

**Virtual members must give the CLI the auth tag too.** On a closed relay an
agent admitted by NIP-AA (never explicitly enrolled) gets
`403 relay_membership_required` from every CLI REST call unless `BUZZ_AUTH_TAG`
is set — the same asymmetry as the HTTP publisher. The WebSocket carries the
credential in the NIP-42 AUTH event; the CLI does not, so it needs the tag in
its environment. `wss://` works fine for the CLI once the tag is present.

## Model: pin it

A freshly-spawned `opencode` with no configured default model silently selects a
wrong one (observed: `google/gemini-3-pro-image-preview`). Interactive clients
set the model per session, so the shared config often has no default — and the
harness spawns *fresh* sessions. Set `BUZZ_ACP_MODEL` in the env so the harness
applies the right model to every new session. Confirm it in the startup banner:
`model=openrouter/moonshotai/kimi-k3`, not `model=(agent default)`.

## Working directory = house rules

Point `WorkingDirectory` at the repo you want the agent operating in. opencode
auto-loads `AGENTS.md` from its cwd every session, so that file's rules are in
front of the model without any buzz-specific wiring. Pair it with a core engram
(`buzz mem set core`) for identity and the non-negotiables the agent must hold
*before* it opens `AGENTS.md`.

## Order of operations

1. Build `buzz-acp` + `buzz` from source; install to `~/.local/bin`.
2. Generate the agent keypair (any Nostr keygen; `nostr.py` can do it with
   `secrets.token_bytes(32)` → `pubkey_xonly`). Store the secret mode-600.
3. Owner runs `owner-setup.py` (their machine, their key): attestation, 30177,
   and `--create-channel` for each private channel.
4. Write the env file from `assets/buzz-acp.env.example` — including
   `BUZZ_AUTH_TAG` and `BUZZ_ACP_MODEL`.
5. Publish the agent profile with `agent-profile.py` (it sends `x-auth-tag`, so
   it works for a virtual member).
6. `buzz mem set core` for the core engram.
7. Install `assets/buzz-acp.service`, `enable-linger`, `enable --now`.
8. Verify: `journalctl --user -u buzz-acp -f` shows owner resolved, channels
   subscribed, model correct. Then the owner @mentions it.

## Verifying the reply path without the owner

Owner-only + private channels mean you cannot easily prompt the bot from another
identity. To prove the agent *can* post before handing off, run the CLI as the
agent against a channel it belongs to:

```bash
BUZZ_PRIVATE_KEY=… BUZZ_AUTH_TAG='…' BUZZ_RELAY_URL=wss://… \
  buzz messages send --channel <uuid> --content "reply path verified"
```

Note: `opencode run "… use buzz to post …"` will **auto-reject** if the shared
`opencode.json` gates `buzz messages send` with `"ask"` and nothing answers.
That is not a failure of the bot — the harness runs `opencode acp` with
`permission_mode=bypassPermissions`, which those gates do not apply to. If you
want the gates to hold for the bot too, you cannot simply flip the harness to
`--permission-mode default` (headless "ask" = auto-deny = the bot goes mute);
instead edit the permission config to allowlist the specific actions the bot may
take autonomously.

## Security note specific to this pattern

A box chosen for red-team or ops work often has reach the Foundation's app hosts
do not — a lab network over Tailscale, live devices, deploy credentials. One bot
across all channels carries that reach in every channel, and `bypassPermissions`
means no per-command stop. The agent's `AGENTS.md` and core engram are then the
main control, and they are *instructions*, not a sandbox. If device-mutating or
irreversible actions must be hard-stopped while unattended, that needs a real
technical boundary (a wrapper that refuses those commands, a separate
lower-privilege bot for the risky channels), not a prompt rule.
