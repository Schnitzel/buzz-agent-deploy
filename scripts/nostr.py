#!/usr/bin/env python3
"""Minimal Nostr toolkit for provisioning buzz agents: BIP-340 signing, NIP-01
event construction, bech32, and NIP-98 authenticated publishing.

Pure stdlib on purpose. These scripts handle a person's signing key, and telling
someone to `pip install` a crypto library before they can paste their nsec into
it is a worse trade than 120 lines of well-tested curve arithmetic. `--selftest`
checks the signer against the official BIP-340 vectors and round-trips an event.

Use as a library (`from nostr import ...`) or run `python3 nostr.py --selftest`.
"""

import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from base64 import b64encode

# ── secp256k1 ────────────────────────────────────────────────────────────────
P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)


def _add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    if p1[0] == p2[0] and p1[1] != p2[1]:
        return None
    if p1 == p2:
        lam = (3 * p1[0] * p1[0] * pow(2 * p1[1], P - 2, P)) % P
    else:
        lam = ((p2[1] - p1[1]) * pow(p2[0] - p1[0], P - 2, P)) % P
    x3 = (lam * lam - p1[0] - p2[0]) % P
    return (x3, (lam * (p1[0] - x3) - p1[1]) % P)


def _mul(point, scalar):
    result = None
    while scalar:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def tagged_hash(tag, msg):
    t = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(t + t + msg).digest()


def schnorr_sign(msg32, seckey32, aux32=b"\x00" * 32):
    d0 = int.from_bytes(seckey32, "big")
    if not 1 <= d0 <= N - 1:
        raise ValueError("secret key out of range")
    point = _mul(G, d0)
    d = d0 if point[1] % 2 == 0 else N - d0
    px = point[0].to_bytes(32, "big")
    t = d ^ int.from_bytes(tagged_hash("BIP0340/aux", aux32), "big")
    rand = tagged_hash("BIP0340/nonce", t.to_bytes(32, "big") + px + msg32)
    k0 = int.from_bytes(rand, "big") % N
    if k0 == 0:
        raise RuntimeError("nonce is zero")
    r = _mul(G, k0)
    k = k0 if r[1] % 2 == 0 else N - k0
    rx = r[0].to_bytes(32, "big")
    e = int.from_bytes(tagged_hash("BIP0340/challenge", rx + px + msg32), "big") % N
    return rx + ((k + e * d) % N).to_bytes(32, "big")


def _lift_x(x):
    if x >= P:
        return None
    y_sq = (pow(x, 3, P) + 7) % P
    y = pow(y_sq, (P + 1) // 4, P)
    if pow(y, 2, P) != y_sq:
        return None
    return (x, y if y % 2 == 0 else P - y)


def schnorr_verify(msg32, pubkey32, sig64):
    """Separate code path from signing on purpose — a mistake in one is then
    not silently mirrored by the other."""
    point = _lift_x(int.from_bytes(pubkey32, "big"))
    if point is None:
        return False
    r = int.from_bytes(sig64[:32], "big")
    s = int.from_bytes(sig64[32:], "big")
    if r >= P or s >= N:
        return False
    e = int.from_bytes(
        tagged_hash("BIP0340/challenge", sig64[:32] + pubkey32 + msg32), "big"
    ) % N
    big_r = _add(_mul(G, s), _mul(point, N - e))
    return big_r is not None and big_r[1] % 2 == 0 and big_r[0] == r


def pubkey_xonly(seckey32):
    return _mul(G, int.from_bytes(seckey32, "big"))[0].to_bytes(32, "big")


# ── bech32 (nsec/npub) ───────────────────────────────────────────────────────
CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _polymod(values):
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        top = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ v
        for i in range(5):
            chk ^= gen[i] if ((top >> i) & 1) else 0
    return chk


def _hrp_expand(hrp):
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _convertbits(data, frm, to, pad=True):
    acc = bits = 0
    ret = []
    maxv = (1 << to) - 1
    for b in data:
        acc = (acc << frm) | b
        bits += frm
        while bits >= to:
            bits -= to
            ret.append((acc >> bits) & maxv)
    if pad and bits:
        ret.append((acc << (to - bits)) & maxv)
    return ret


def bech32_decode(s):
    s = s.strip().lower()
    if "1" not in s:
        raise ValueError("not bech32")
    hrp, data = s.rsplit("1", 1)
    dec = [CHARSET.index(c) for c in data]
    if _polymod(_hrp_expand(hrp) + dec) != 1:
        raise ValueError("bad bech32 checksum")
    return hrp, bytes(_convertbits(dec[:-6], 5, 8, False))


def bech32_encode(hrp, data_bytes):
    data = _convertbits(data_bytes, 8, 5)
    chk = _polymod(_hrp_expand(hrp) + data + [0] * 6) ^ 1
    return hrp + "1" + "".join(CHARSET[d] for d in data + [(chk >> (5 * (5 - i))) & 31 for i in range(6)])


def to_hex_seckey(raw):
    """Accept an nsec or bare hex and return 32 raw bytes."""
    raw = raw.strip()
    if raw.startswith("nsec"):
        hrp, sk = bech32_decode(raw)
        if hrp != "nsec":
            raise SystemExit(f"expected an nsec, got {hrp}")
    else:
        sk = bytes.fromhex(raw)
    if len(sk) != 32:
        raise SystemExit("secret key must be 32 bytes")
    return sk


# ── NIP-01 events ────────────────────────────────────────────────────────────
def serialize_for_id(pubkey_hex, created_at, kind, tags, content):
    """[0, pubkey, created_at, kind, tags, content], compact, no whitespace."""
    return json.dumps(
        [0, pubkey_hex, created_at, kind, tags, content],
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def build_event(sk, kind, tags, content, created_at=None):
    pk = pubkey_xonly(sk).hex()
    created_at = int(time.time()) if created_at is None else created_at
    eid = hashlib.sha256(serialize_for_id(pk, created_at, kind, tags, content)).digest()
    return {
        "id": eid.hex(),
        "pubkey": pk,
        "created_at": created_at,
        "kind": kind,
        "tags": tags,
        "content": content,
        "sig": schnorr_sign(eid, sk).hex(),
    }


# ── Publishing ───────────────────────────────────────────────────────────────
KIND_HTTP_AUTH = 27235


def publish(sk, event, relay_url, verbose=True, auth_tag=None):
    """POST a signed event to a buzz relay's /events, authorized by NIP-98.

    The relay requires the event's author to equal the authenticated identity
    ("event pubkey does not match authenticated identity"), so `sk` must be the
    key that signed `event`. You cannot publish on someone else's behalf.

    `auth_tag` is the NIP-OA credential, sent as the `x-auth-tag` header. On a
    closed relay an agent that holds only NIP-AA *virtual* membership — never
    explicitly enrolled — is rejected with

        403 {"error":"relay_membership_required"}

    unless this header is present. The WebSocket path carries the credential in
    the NIP-42 AUTH event instead, so the agent connects happily while HTTP
    publishing fails: an easy asymmetry to misread as a broken key.
    """
    url = relay_url.rstrip("/") + "/events"
    body = json.dumps(event, separators=(",", ":")).encode("utf-8")
    auth = build_event(
        sk,
        KIND_HTTP_AUTH,
        [["u", url], ["method", "POST"], ["payload", hashlib.sha256(body).hexdigest()]],
        "",
    )
    header = "Nostr " + b64encode(
        json.dumps(auth, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    headers = {"Content-Type": "application/json", "Authorization": header}
    if auth_tag:
        headers["x-auth-tag"] = (
            auth_tag if isinstance(auth_tag, str)
            else json.dumps(auth_tag, separators=(",", ":"))
        )
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            out = resp.read().decode("utf-8", "replace")
            if '"accepted":true' in out.replace(" ", ""):
                return True
            if verbose:
                print(f"      relay {resp.status}: {out}")
            return False
    except urllib.error.HTTPError as e:
        if verbose:
            print(f"      relay refused HTTP {e.code}: {e.read().decode('utf-8', 'replace')}")
        return False
    except urllib.error.URLError as e:
        if verbose:
            print(f"      could not reach {url}: {e.reason}")
        return False


# ── Self-test ────────────────────────────────────────────────────────────────
def selftest():
    vectors = [
        (
            "0000000000000000000000000000000000000000000000000000000000000003",
            "0000000000000000000000000000000000000000000000000000000000000000",
            "0000000000000000000000000000000000000000000000000000000000000000",
            "E907831F80848D1069A5371B402410364BDF1C5F8307B0084C55F1CE2DCA8215"
            "25F66A4A85EA8B71E482A74F382D2CE5EBEEE8FDB2172F477DF4900D310536C0",
        ),
        (
            "B7E151628AED2A6ABF7158809CF4F3C762E7160F38B4DA56A784D9045190CFEF",
            "0000000000000000000000000000000000000000000000000000000000000001",
            "243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89",
            "6896BD60EEAE296DB48A229FF71DFE071BDE413E6D43F917DC8DCF8C78DE3341"
            "8906D11AC976ABCCB20B091292BFF4EA897EFCB639EA871CFA95F6DE339E4B0A",
        ),
    ]
    ok = True
    for i, (sk, aux, msg, want) in enumerate(vectors):
        got = schnorr_sign(bytes.fromhex(msg), bytes.fromhex(sk), bytes.fromhex(aux))
        if got.hex().upper() != want:
            ok = False
            print(f"BIP-340 vector {i}: FAIL")
        elif not schnorr_verify(
            bytes.fromhex(msg), pubkey_xonly(bytes.fromhex(sk)), got
        ):
            ok = False
            print(f"BIP-340 vector {i}: signature did not verify")
        else:
            print(f"BIP-340 vector {i}: ok")

    sk = hashlib.sha256(b"selftest").digest()
    ev = build_event(sk, 1, [["t", "test"]], "hello")
    rebuilt = hashlib.sha256(
        serialize_for_id(ev["pubkey"], ev["created_at"], ev["kind"], ev["tags"], ev["content"])
    ).hexdigest()
    if rebuilt == ev["id"] and schnorr_verify(
        bytes.fromhex(ev["id"]), bytes.fromhex(ev["pubkey"]), bytes.fromhex(ev["sig"])
    ):
        print("event id + signature round trip: ok")
    else:
        ok = False
        print("event id + signature round trip: FAIL")

    npub = bech32_encode("npub", bytes.fromhex(ev["pubkey"]))
    if bech32_decode(npub)[1].hex() == ev["pubkey"]:
        print("bech32 round trip: ok")
    else:
        ok = False
        print("bech32 round trip: FAIL")

    print("selftest: PASS" if ok else "selftest: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else selftest())


def query(sk, filters, relay_url, auth_tag=None, timeout=25):
    """Read events from the relay's HTTP bridge, authenticated as `sk`.

    Nostr filters, NIP-98 (kind 27235) HTTP auth. Returns a list of events.

    `auth_tag` is the NIP-OA attestation JSON. A virtual member has no
    relay_members row of its own and is admitted only through its owner, so
    without the tag the bridge answers 403 relay_membership_required — the
    same trap as the CLI. Harmless to pass for an enrolled agent.
    """
    url = relay_url.rstrip("/") + "/query"
    body = json.dumps(filters).encode()
    auth = build_event(
        sk,
        27235,
        [["u", url],
         ["method", "POST"],
         ["payload", hashlib.sha256(body).hexdigest()]],
        "",
    )
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Nostr " + b64encode(
            json.dumps(auth, separators=(",", ":")).encode()).decode(),
    }
    if auth_tag:
        headers["x-auth-tag"] = auth_tag
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    payload = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    return payload if isinstance(payload, list) else payload.get("events", [])
