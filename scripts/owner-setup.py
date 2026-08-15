#!/usr/bin/env python3
"""Everything about a buzz agent that only the OWNER's key can authorize.

Three things, all signed by the workspace owner and published from their
machine, because the relay rejects any event whose author is not the connection
that submitted it ("event pubkey does not match authenticated identity"). No
amount of server-side access substitutes for this.

  1. NIP-OA `auth` tag       → the "agent, managed by <you>" badge
  2. kind 30177              → the agent appears in Buzz Desktop's Agents panel
  3. kind 9000 role=bot      → seats the agent in a channel AS A BOT, which is
                               half of what makes it @-mentionable

Buzz Desktop does all three automatically for agents it creates itself. An agent
provisioned anywhere else gets none of them, with no error saying so.

USAGE

    python3 owner-setup.py --selftest
    python3 owner-setup.py \
        --relay https://buzz.example.org \
        --agent <agent-pubkey-hex> \
        --name my-agent \
        --channel general=<uuid> --channel ops=<uuid> \
        [--dry-run]

Your key is typed at a hidden prompt — never an argument, so it stays out of
shell history — used in memory, and never printed. The `auth` tag is printed
because it is public: it is a signature, published on every event the agent
sends. Store it and pass it to the agent as BUZZ_AUTH_TAG.

Re-running is safe. Kind 30177 is replaceable and keyed by the agent's pubkey,
and kind 9000 is an idempotent add.
"""

import argparse
import hashlib
import json
import os
import sys
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nostr import (  # noqa: E402
    build_event,
    publish,
    pubkey_xonly,
    schnorr_sign,
    schnorr_verify,
    selftest as crypto_selftest,
    to_hex_seckey,
)

KIND_MANAGED_AGENT = 30177
KIND_PUT_USER = 9000


def make_auth_tag(sk, agent_pubkey, conditions=""):
    """NIP-OA owner attestation.

    Preimage is `nostr:agent-auth:` || agent-pubkey || `:` || conditions, and
    the signed message is its SHA256. Because it commits to the agent's KEY and
    not to any particular event, one tag is a reusable capability covering
    everything the agent ever publishes — mint once, reuse forever, redo only
    if the agent is re-keyed.
    """
    preimage = f"nostr:agent-auth:{agent_pubkey}:{conditions}".encode()
    sig = schnorr_sign(hashlib.sha256(preimage).digest(), sk)
    return ["auth", pubkey_xonly(sk).hex(), conditions, sig.hex()]


def verify_auth_tag(tag, agent_pubkey):
    if len(tag) != 4 or tag[0] != "auth":
        return False
    _, owner, conditions, sig = tag
    if owner == agent_pubkey:  # self-attestation is invalid per NIP-OA
        return False
    msg = hashlib.sha256(
        f"nostr:agent-auth:{agent_pubkey}:{conditions}".encode()
    ).digest()
    return schnorr_verify(msg, bytes.fromhex(owner), bytes.fromhex(sig))


def selftest():
    rc = crypto_selftest()
    sk = hashlib.sha256(b"owner").digest()
    agent = hashlib.sha256(b"agent").hexdigest()
    tag = make_auth_tag(sk, agent)
    print("auth tag verifies:", verify_auth_tag(tag, agent))
    print("rejects a different agent key:", not verify_auth_tag(tag, "aa" * 32))
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--relay", help="relay base URL, e.g. https://buzz.example.org")
    ap.add_argument("--agent", help="agent pubkey, 64-char hex")
    ap.add_argument("--name", default="agent", help="display name for the Agents panel")
    ap.add_argument(
        "--channel",
        action="append",
        default=[],
        metavar="NAME=UUID",
        help="channel to seat the agent in as a bot; repeatable",
    )
    ap.add_argument("--parallelism", type=int, default=1)
    # NIP-AP: kind 30177 is the per-instance record. A "definition-less"
    # instance — one not spawned from a kind 30175 persona, which is what a
    # hand-provisioned server agent is — is its own definition, and the spec
    # says writers MUST keep emitting the definition-level fields for it. A
    # definition-linked instance resolves them from its persona and self-heals;
    # a definition-less one has no restore path, so omitting them loses state.
    ap.add_argument("--model", default=None, help="e.g. openrouter/moonshotai/kimi-k3")
    ap.add_argument("--provider", default=None, help="e.g. openrouter")
    ap.add_argument("--system-prompt", default=None)
    ap.add_argument("--persona-id", default=None,
                    help="link to a kind 30175 persona; omit for a standalone agent")
    ap.add_argument("--reason", default="retired",
                    help="NIP-IA reason code: rotated, retired, bot-rebuilt, "
                         "left-organization, spam")
    ap.add_argument("--archive", action="store_true",
                    help="retire the agent instead (NIP-IA kind 9035) — do this "
                         "BEFORE destroying its key, or nothing can clean up after it")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not (args.relay and args.agent):
        ap.error("--relay and --agent are required")
    if len(args.agent) != 64:
        ap.error("--agent must be 64-char hex")

    print(f"Authorizing agent {args.agent[:16]}… on {args.relay}\n")
    sk = to_hex_seckey(getpass("Owner Nostr secret key (nsec/hex, hidden): "))
    owner = pubkey_xonly(sk).hex()
    if owner == args.agent:
        raise SystemExit("that is the agent's own key, not the owner's")
    print(f"Signing as {owner}\n")

    auth_tag = make_auth_tag(sk, args.agent)
    assert verify_auth_tag(auth_tag, args.agent), "generated an invalid auth tag"

    if args.archive:
        # NIP-IA kind 9035. Required tags are exactly one `p` naming the target
        # and exactly one NIP-70 `-` tag marking the request protected — the
        # relay rejects it outright without the latter:
        #   "request must include exactly one NIP-70 protected event tag"
        # The `auth` tag is what proves owner authority over the target, so the
        # relay records the consent path as "owner" rather than "self".
        ev = build_event(
            sk,
            9035,
            [["-"], ["p", args.agent], ["reason", args.reason], auth_tag],
            "retired by owner",
        )
        ok = publish(sk, ev, args.relay)
        print(f"  {'ok  ' if ok else 'FAIL'}  kind 9035  archive {args.agent[:16]}…")
        if not ok:
            print("\nArchival is relay policy, not a protocol guarantee: a relay MAY\n"
                  "accept only `self` or `admin` consent paths. See docs/nips/NIP-IA.md.")
        return 0 if ok else 1

    # Instance-level fields, always present.
    record = {
        "name": args.name,
        "parallelism": args.parallelism,
        "respond_to": "allowlist",
        "respond_to_allowlist": [owner],
    }
    if args.persona_id:
        # Definition-linked: the persona is authoritative, and NIP-AP says
        # writers MUST NOT duplicate definition-level fields here.
        record["persona_id"] = args.persona_id
    else:
        # Definition-less: this record IS the definition, so carry them.
        for key, value in (
            ("model", args.model),
            ("provider", args.provider),
            ("system_prompt", args.system_prompt),
        ):
            if value:
                record[key] = value
        missing = [k for k in ("model", "provider") if k not in record]
        if missing:
            print(f"note: standalone instance with no {', '.join(missing)} — "
                  "harmless, but NIP-AP expects definition-level fields on an "
                  "agent that has no persona to resolve them from.\n")

    # The content body is public and unencrypted. NIP-AP: it MUST NOT carry
    # secrets, and env_vars MUST NOT appear. Nothing here should ever hold one.
    assert "env_vars" not in record
    events = [
        (
            "kind 30177  managed-agent record (Agents panel)",
            build_event(sk, KIND_MANAGED_AGENT, [["d", args.agent]], json.dumps(record)),
        )
    ]
    for spec in args.channel:
        name, _, cid = spec.partition("=")
        if not cid:
            ap.error(f"--channel wants NAME=UUID, got {spec!r}")
        events.append(
            (
                f"kind 9000   seat as bot in #{name}",
                build_event(
                    sk, KIND_PUT_USER,
                    [["h", cid.strip()], ["p", args.agent], ["role", "bot"]], "",
                ),
            )
        )

    print("BUZZ_AUTH_TAG (public — give this to the agent):\n")
    print(json.dumps(auth_tag))
    print("\nWill publish:")
    for label, _ in events:
        print(f"  {label}")

    if args.dry_run:
        print("\n--dry-run: nothing sent.")
        return 0

    print()
    failures = 0
    for label, ev in events:
        ok = publish(sk, ev, args.relay)
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
        failures += 0 if ok else 1

    if failures:
        print(f"\n{failures} of {len(events)} failed.")
        print("A private channel needs the OWNER or an admin — self-add is refused there.")
        return 1
    print("\nDone. Now run agent-profile.py as the AGENT, then restart Buzz Desktop.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
