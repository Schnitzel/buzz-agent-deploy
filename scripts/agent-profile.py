#!/usr/bin/env python3
"""Publish the agent's own two profile events, signed by the AGENT's key.

  kind 0      display name + bio, carrying the NIP-OA auth tag if you have one
  kind 10100  the relay-agent directory entry

The kind 0 profile is required — it is what gives the agent a name instead of a
hex string, and it carries the owner attestation.

The kind 10100 is **optional and undocumented**. No NIP defines it, and
upstream's own reference bot (`examples/countdown-bot`) never publishes one:
there, a kind 9000 self-add with `role=bot` is what makes a bot appear in
mention autocomplete. Publish the 10100 only if `role=bot` seating alone does
not get the agent into autocomplete.

It exists because Buzz Desktop has a second route to mentionability: an agent
in the relay-agent directory passing `relayAgentCanRespondInChannel`, which
needs `channel_ids` to contain the channel and a `respond_to` admitting the
person typing. Set BUZZ_AGENT_SKIP_DIRECTORY=1 to publish only the profile.

⚠ Kind 10100 is REPLACEABLE, and `buzz channels set-add-policy` publishes a
10100 containing only `{"channel_add_policy": ...}`. Running that command
therefore wipes the profile and silently un-mentions the agent. Re-run this
script to repair it, and prefer running it on every deploy so the repair is
automatic.

Everything comes from the environment so no secret is ever an argument:

    BUZZ_AGENT_SECKEY    agent secret key, nsec or hex     (required)
    BUZZ_RELAY           relay base URL                    (required)
    BUZZ_AGENT_NAME      display name                      (required)
    BUZZ_AGENT_OWNER     owner pubkey hex, for respond_to  (required)
    BUZZ_AGENT_CHANNELS  "name=uuid,name=uuid"             (required)
    BUZZ_AGENT_AUTHTAG   NIP-OA auth tag JSON              (optional)
    BUZZ_AGENT_ABOUT     bio                               (optional)
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nostr import build_event, publish, to_hex_seckey  # noqa: E402

KIND_PROFILE = 0
KIND_AGENT_DIRECTORY = 10100


def main():
    try:
        sk = to_hex_seckey(os.environ["BUZZ_AGENT_SECKEY"])
        relay = os.environ["BUZZ_RELAY"].rstrip("/")
        name = os.environ["BUZZ_AGENT_NAME"]
        owner = os.environ["BUZZ_AGENT_OWNER"].strip()
        channels_raw = os.environ["BUZZ_AGENT_CHANNELS"]
    except KeyError as e:
        print(f"missing required environment variable: {e}", file=sys.stderr)
        return 2

    names, ids = [], []
    for pair in channels_raw.split(","):
        if not pair.strip():
            continue
        n, _, cid = pair.partition("=")
        names.append(n.strip())
        ids.append(cid.strip())
    if not ids:
        print("BUZZ_AGENT_CHANNELS listed no channels — the agent will not be "
              "mentionable anywhere", file=sys.stderr)

    tags = []
    authtag = os.environ.get("BUZZ_AGENT_AUTHTAG", "").strip()
    if authtag:
        tags.append(json.loads(authtag))
    else:
        print("WARNING: no BUZZ_AGENT_AUTHTAG. The agent will work and will be\n"
              "         mentionable, but buzz will show it as 'owner unavailable'\n"
              "         — a bot in your channels that nobody is accountable for.\n"
              "         Mint one with owner-setup.py unless this is throwaway.\n")

    about = os.environ.get("BUZZ_AGENT_ABOUT", "").strip()
    profile = {"display_name": name}
    if about:
        profile["about"] = about

    directory = {
        "name": name,
        "agent_type": "agent",
        "channels": names,
        "channel_ids": ids,
        "capabilities": [],
        "status": "online",
        # Mirror the harness's own author gate: the people who may prompt it
        # are then exactly the people whose autocomplete offers it.
        "respond_to": "allowlist",
        "respond_to_allowlist": [owner],
    }

    events = [
        ("kind 0      profile", build_event(sk, KIND_PROFILE, tags, json.dumps(profile))),
    ]
    if os.environ.get("BUZZ_AGENT_SKIP_DIRECTORY", "").strip() not in ("1", "true", "yes"):
        events.append(
            ("kind 10100  relay-agent directory (optional)",
             build_event(sk, KIND_AGENT_DIRECTORY, tags,
                         json.dumps(directory, separators=(",", ":"))))
        )

    failures = 0
    for label, ev in events:
        ok = publish(sk, ev, relay, auth_tag=authtag or None)
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
        failures += 0 if ok else 1
    if failures:
        return 1
    print(f"published for {events[0][1]['pubkey'][:16]}… in {len(ids)} channel(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
