# auth/selftest.py — offline verification of the Phase-0 auth machinery.
#
# No real Clerk tenant and NO database needed: we mint our own RS256 tokens against a
# throwaway keypair and feed the verifier a stub JWKS client, so jwt.decode() does REAL
# signature + issuer + expiry verification. The principal/membership resolution is then
# exercised with a stubbed iam repository + fake DB session, so resolve_principal() runs
# end-to-end (verify -> upsert user -> load memberships -> resolve club_id+role) with no
# network and no Postgres.
#
# Run:
#   python -m auth.selftest          # crypto + principal/tenancy + OPS checks (NO DB)
#
# This is the Phase-0 "JWT resolves a principal" gate's offline twin — the live gate
# additionally needs DATABASE_URL + the real Clerk app (see BUILD_REPORT).

import os
import sys
import time

# ---- token + key helpers -------------------------------------------------

def _mint_keypair():
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, key.public_key()


def _make_token(priv, *, iss, sub, email=None, exp_delta=300, aud=None, kid="test-kid"):
    import jwt
    now = int(time.time())
    claims = {"iss": iss, "sub": sub, "iat": now, "exp": now + exp_delta}
    if email is not None:
        claims["email"] = email
    if aud is not None:
        claims["aud"] = aud
    return jwt.encode(claims, priv, algorithm="RS256", headers={"kid": kid})


class _StubJWKS:
    """Stands in for PyJWKClient — returns our public key for any token."""
    def __init__(self, pub):
        self._pub = pub

    def get_signing_key_from_jwt(self, token):
        class _K:
            key = self._pub
        return _K()


class _CI(dict):
    """Minimal case-insensitive header map supporting .get(key, default)."""
    def __init__(self, d):
        super().__init__({k.lower(): v for k, v in d.items()})

    def get(self, k, default=None):
        return super().get(k.lower(), default)


class _FakeRequest:
    def __init__(self, headers=None):
        self.headers = _CI(headers or {})
        self.host = self.headers.get("Host", "")


# ---- a fake iam repo + session so principal resolution runs without a DB ----

class _FakeSession:
    """No-op transactional session; the stubbed repo ignores it."""
    def commit(self): pass
    def rollback(self): pass
    def close(self): pass


def _install_db_stub(monkey_state):
    """Patch db.session_scope + iam.repositories so _principal_from_claims runs offline.
    monkey_state holds the in-memory 'memberships' + 'host_map' the test controls."""
    import contextlib
    import db
    from iam import repositories as iam_repo

    @contextlib.contextmanager
    def _fake_scope():
        yield _FakeSession()

    db.session_scope = _fake_scope

    def _upsert(session, *, clerk_user_id, email=None, first_name=None, surname=None, phone=None):
        return {"id": "user-" + (clerk_user_id or "x"), "clerk_user_id": clerk_user_id,
                "email": email, "first_name": first_name, "surname": surname}

    iam_repo.upsert_user_by_clerk_id = _upsert
    iam_repo.memberships_for_user = lambda session, user_id: monkey_state["memberships"]
    iam_repo.resolve_club_by_host = lambda session, host: monkey_state["host_map"].get(
        (host or "").split(":", 1)[0].lower())


# ---- assertions ----------------------------------------------------------

_passed = 0
_failed = 0


def _check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}")


def run():
    from auth import verifier, principal

    iss = "https://selftest.clerk.example"
    priv, pub = _mint_keypair()

    os.environ["AUTH_ENABLED"] = "1"
    os.environ["AUTH_PROVIDER"] = "clerk"
    os.environ["AUTH_ISSUER"] = iss
    os.environ["AUTH_JWKS_URL"] = "https://selftest/.well-known/jwks.json"  # stubbed, not fetched
    os.environ.pop("AUTH_AUDIENCE", None)
    verifier._get_jwks_client = lambda: _StubJWKS(pub)

    print("verifier:")
    _check("looks_like_jwt rejects opaque key", not verifier.looks_like_jwt("plain-ops-key"))
    good = _make_token(priv, iss=iss, sub="user_abc", email="a@b.com")
    _check("looks_like_jwt accepts a JWT", verifier.looks_like_jwt(good))
    _check("valid token verifies + carries claims",
           (verifier.verify_jwt(good) or {}).get("sub") == "user_abc")
    _check("email claim extracted", verifier.claim_email(verifier.verify_jwt(good)) == "a@b.com")

    bad_iss = _make_token(priv, iss="https://evil", sub="user_abc", email="a@b.com")
    _check("wrong issuer rejected", verifier.verify_jwt(bad_iss) is None)
    expired = _make_token(priv, iss=iss, sub="user_abc", email="a@b.com", exp_delta=-120)
    _check("expired token rejected", verifier.verify_jwt(expired) is None)
    other_priv, _ = _mint_keypair()
    forged = _make_token(other_priv, iss=iss, sub="user_abc", email="a@b.com")
    _check("bad signature rejected", verifier.verify_jwt(forged) is None)

    os.environ["AUTH_ENABLED"] = "0"
    _check("AUTH_ENABLED=0 -> verify_jwt is a no-op", verifier.verify_jwt(good) is None)
    os.environ["AUTH_ENABLED"] = "1"

    # ---- principal + tenancy resolution (offline DB stub) ----
    print("principal / tenancy:")
    state = {"memberships": [], "host_map": {}}
    _install_db_stub(state)

    NP = "club-nextpoint"
    AC = "club-academy"

    # single membership -> resolved by default even without a host match
    state["memberships"] = [{"club_id": NP, "user_id": "u", "role": "member",
                             "member_status": "active"}]
    state["host_map"] = {"nextpointtennis.com": NP}
    p = principal.resolve_principal(_FakeRequest(headers={"Authorization": f"Bearer {good}"}))
    _check("single membership resolves principal", p is not None and p.method == "jwt")
    _check("club_id resolved", p is not None and p.club_id == NP)
    _check("role resolved", p is not None and p.role == "member")
    _check("email derived server-side", p is not None and p.email == "a@b.com")

    # host disambiguates among multiple memberships
    state["memberships"] = [
        {"club_id": NP, "user_id": "u", "role": "coach", "member_status": "active"},
        {"club_id": AC, "user_id": "u", "role": "member", "member_status": "active"},
    ]
    p2 = principal.resolve_principal(_FakeRequest(
        headers={"Authorization": f"Bearer {good}", "Host": "nextpointtennis.com"}))
    _check("host selects the matching club", p2 is not None and p2.club_id == NP and p2.role == "coach")

    # X-Club header overrides host (admin switcher), validated against memberships
    p3 = principal.resolve_principal(_FakeRequest(
        headers={"Authorization": f"Bearer {good}", "Host": "nextpointtennis.com", "X-Club": AC}))
    _check("X-Club overrides host", p3 is not None and p3.club_id == AC and p3.role == "member")

    # X-Club for a club the user is NOT a member of -> no role granted
    p4 = principal.resolve_principal(_FakeRequest(
        headers={"Authorization": f"Bearer {good}", "X-Club": "club-stranger"}))
    _check("X-Club not in memberships -> no club/role", p4 is not None and p4.role is None)

    # invalid JWT is rejected, never downgraded to OPS
    os.environ["OPS_KEY"] = "ops-secret"
    rej = principal.resolve_principal(_FakeRequest(
        headers={"Authorization": f"Bearer {bad_iss}", "X-Ops-Key": "ops-secret"}))
    _check("invalid JWT rejected (not downgraded to OPS)", rej is None)

    # ---- OPS_KEY server-to-server path ----
    print("ops key:")
    # disable JWT so a non-JWT bearer/header reaches the OPS path cleanly
    os.environ["AUTH_ENABLED"] = "0"
    ops = principal.resolve_principal(_FakeRequest(headers={"X-Ops-Key": "ops-secret"}))
    _check("OPS key resolves platform_admin", ops is not None and ops.method == "ops"
           and ops.role == "platform_admin")
    bad_ops = principal.resolve_principal(_FakeRequest(headers={"X-Ops-Key": "wrong"}))
    _check("wrong OPS key rejected", bad_ops is None)
    no_auth = principal.resolve_principal(_FakeRequest(headers={}))
    _check("no credentials -> None", no_auth is None)


def main():
    run()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
