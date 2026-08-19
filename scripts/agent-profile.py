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
    BUZZ_AGENT_CHANNELS  "name=uuid,name=uuid", or "auto"  (required)
    BUZZ_AGENT_ADD_POLICY  anyone|owner_only|nobody         (optional)
    BUZZ_AGENT_ALLOWLIST comma-separated hex pubkeys        (optional)
    BUZZ_AGENT_RESPOND_TO  allowlist|anyone                  (optional)
    BUZZ_AGENT_AUTHTAG   NIP-OA auth tag JSON              (optional)
    BUZZ_AGENT_ABOUT     bio                               (optional)
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nostr import build_event, publish, query, to_hex_seckey  # noqa: E402
from nostr import pubkey_xonly  # noqa: E402

KIND_PROFILE = 0
KIND_AGENT_DIRECTORY = 10100


def discover_channels(sk, relay, auth_tag):
    """Ask the relay which channels the agent is actually in.

    Reads the relay's own signed rosters (kind 39002, `d` = channel id, one `p`
    per member) filtered to this agent, then kind 39000 for display names.

    Why this beats a hand-written list: `channel_ids` is checked against the
    channel being typed in, so the moment someone adds the agent to a channel
    that is not in the list, the agent is a member nobody can mention there —
    with no error on either side. A list maintained by hand is stale from the
    first time anyone touches channel membership without redeploying. The relay
    cannot drift from itself.
    """
    me = pubkey_xonly(sk).hex()

    def tagmap(ev):
        return {t[0]: t[1] for t in ev.get("tags", []) if len(t) >= 2}

    ids, seen = [], set()
    for ev in query(sk, [{"kinds": [39002], "#p": [me]}], relay, auth_tag):
        cid = tagmap(ev).get("d")
        if cid and cid not in seen:
            seen.add(cid)
            ids.append(cid)

    names = {}
    for ev in query(sk, [{"kinds": [39000]}], relay, auth_tag):
        tm = tagmap(ev)
        if "d" in tm:
            names[tm["d"]] = tm.get("name", "channel")

    return [(names.get(i, "channel"), i) for i in ids]


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

    authtag = os.environ.get("BUZZ_AGENT_AUTHTAG", "").strip()

    # "allowlist" names who may mention it; "anyone" drops the list and lets
    # every member of a channel the agent is in mention it, with no pubkeys to
    # collect. That is channel-scoped, not relay-wide: the client also requires
    # the viewer to share one of the agent's channels. Only these two values are
    # meaningful here — the client tests for exactly them, so publishing
    # anything else (including "owner-only") hides the agent from everyone,
    # owner included.
    respond_to = os.environ.get("BUZZ_AGENT_RESPOND_TO", "allowlist").strip() or "allowlist"
    if respond_to not in ("allowlist", "anyone"):
        print(f"BUZZ_AGENT_RESPOND_TO must be allowlist or anyone (got {respond_to!r}); "
              "any other value publishes an entry nobody can see", file=sys.stderr)
        return 2

    # ⚠ This list is checked against the pubkey of the person TYPING, not the
    # agent's owner. Buzz Desktop hides the agent from anyone absent from it
    # (relayAgentIsSharedWithUser), so it MUST mirror the harness's own
    # BUZZ_ACP_RESPOND_TO_ALLOWLIST. Publish only the owner while the harness
    # gate admits a teammate and the agent is invisible in their autocomplete
    # even though it would answer them — they get no error, and a mention they
    # type by hand goes out with no `p` tag and routes nowhere.
    allowlist = [k.strip() for k in
                 os.environ.get("BUZZ_AGENT_ALLOWLIST", "").split(",") if k.strip()]
    if owner not in allowlist:
        allowlist.insert(0, owner)
    bad = [k for k in allowlist if len(k) != 64 or not all(c in "0123456789abcdef" for c in k.lower())]
    if bad:
        print(f"BUZZ_AGENT_ALLOWLIST needs 64-char hex pubkeys; bad entries: {bad}",
              file=sys.stderr)
        return 2

    # Who may add this agent to a channel. "anyone" matches the relay's own
    # column default and keeps teammates able to pull the agent into their
    # channels; "owner_only" stops anyone but you seating it somewhere it will
    # then read. Republished every time, so this is also how you change it.
    add_policy = os.environ.get("BUZZ_AGENT_ADD_POLICY", "anyone").strip() or "anyone"
    if add_policy not in ("anyone", "owner_only", "nobody"):
        print(f"BUZZ_AGENT_ADD_POLICY must be anyone, owner_only or nobody "
              f"(got {add_policy!r})", file=sys.stderr)
        return 2

    names, ids = [], []
    if channels_raw.strip().lower() == "auto":
        try:
            pairs = discover_channels(sk, relay, authtag or None)
        except Exception as e:
            print(f"channel discovery failed: {e}\n"
                  "Refusing to publish: a directory entry built from a partial "
                  "channel list would silently un-mention the agent in every "
                  "channel it omits.", file=sys.stderr)
            return 1
        names = [n for n, _ in pairs]
        ids = [i for _, i in pairs]
    else:
        for pair in channels_raw.split(","):
            if not pair.strip():
                continue
            n, _, cid = pair.partition("=")
            names.append(n.strip())
            ids.append(cid.strip())
    if not ids:
        where = ("the relay reports the agent in no channels"
                 if channels_raw.strip().lower() == "auto"
                 else "BUZZ_AGENT_CHANNELS listed no channels")
        print(f"{where} — the agent will not be mentionable anywhere",
              file=sys.stderr)

    tags = []
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
        "respond_to": respond_to,
        "respond_to_allowlist": allowlist,
        # REQUIRED by the relay even though the entry is otherwise free-form.
        # handle_agent_profile() reads this field to set users.channel_add_policy
        # and errors without it — "Side effect failed: kind:10100 missing
        # channel_add_policy field". The relay logs that and still answers the
        # publisher `accepted: true`, so omitting it looks like a clean publish
        # and quietly leaves the policy at whatever it already was.
        "channel_add_policy": add_policy,
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
