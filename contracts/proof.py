# v0.3.0
# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }
"""
PROOF. Showing you control something off chain, with nobody's say-so.

An address claims a resource, publishes one line where only its controller
could put it, and every validator fetches that document and checks. There is
no issuer, no signature to trust, and nothing the claimant can tell the
contract that it does not check for itself.

    claim_domain(domain)          -> the line to publish, and where
    claim_handle(handle, gist)    -> the line to publish, in that gist
    check(claim_id)               -> fetch it, under consensus
    get_claim(claim_id)           -> what consensus agreed, as a free view

Two resources, because they fail differently and that is the interesting
part. A domain is proven by a file at a fixed path, so the only question is
whether the bytes are there. A gist is proven through GitHub's API, where
the token proves somebody wrote the file and the `owner` field proves who,
and a claim is only good if both hold.

The three outcomes are kept apart on purpose:

    verified     fetched, and the token was on a line of its own
    missing      fetched, and the token was not there
    unreachable  could not be fetched at all

`missing` and `unreachable` are different claims about the world, and
downstream one of them costs somebody money. A site that is briefly down
must never look like a claimant who withdrew their proof.

No model is involved anywhere. Validators fetch and compare strings.
"""

from dataclasses import dataclass

import genlayer as gl
from genlayer.types import *

from contracts.resource import (
    MISSING,
    UNREACHABLE,
    VERIFIED,
    domain_url,
    gist_owner,
    gist_text,
    gist_url,
    same_handle,
    token_for,
    token_present,
    valid_domain,
    valid_gist_id,
    valid_handle,
)

KIND_DOMAIN = "domain"
KIND_HANDLE = "handle"


@gl.storage.allow
@dataclass
class Claim:
    owner: Address
    kind: str
    resource: str
    # Only a handle claim uses this: which gist carries the token.
    locator: str
    token: str
    url: str
    opened_at: str
    # "" until the first check. Then verified, missing or unreachable.
    status: str
    checked_at: str
    note: str


class Proof(gl.contract.Contract):
    claims: gl.storage.TreeMap[str, Claim]
    claim_ids: gl.storage.DynArray[str]

    def __init__(self):
        pass

    # ----------------------------------------------------------------
    # opening a claim
    # ----------------------------------------------------------------

    @gl.public.write
    def claim_domain(self, domain: str) -> dict:
        """
        Start proving control of a domain.

        Returns the exact line to publish and the exact URL it will be read
        from. Both are derived here, so a claimant cannot point the contract
        at a document they merely influence.
        """
        name = domain.strip().lower()
        if not valid_domain(name):
            raise gl.vm.UserError("a domain is a bare hostname, with no scheme, port or path")
        return self._open(KIND_DOMAIN, name, "", domain_url(name))

    @gl.public.write
    def claim_handle(self, handle: str, gist_id: str) -> dict:
        """
        Start proving control of a GitHub account, through a gist.

        The gist id is the claimant's to choose, which is safe because the
        check reads the owner out of the response: pointing at somebody
        else's gist proves that somebody else controls the account, not you.
        """
        name = handle.strip()
        if not valid_handle(name):
            raise gl.vm.UserError("a handle is GitHub's own username shape")
        gist = gist_id.strip()
        if not valid_gist_id(gist):
            raise gl.vm.UserError("a gist id is hexadecimal")
        return self._open(KIND_HANDLE, name.lower(), gist, gist_url(gist))

    def _open(self, kind: str, resource: str, locator: str, url: str) -> dict:
        owner = gl.message.sender_address
        claim_id = owner.as_hex.lower() + ":" + kind + ":" + resource
        if claim_id in self.claims:
            # Re-opening would reset a proof somebody downstream is relying
            # on. Checking it again is free and is what they want anyway.
            raise gl.vm.UserError("that claim already exists; check it instead: " + claim_id)

        token = token_for(owner.as_hex, kind, resource)
        self.claims[claim_id] = Claim(
            owner=owner,
            kind=kind,
            resource=resource,
            locator=locator,
            token=token,
            url=url,
            opened_at=_stamp(),
            status="",
            checked_at="",
            note="publish the token, then call check",
        )
        self.claim_ids.append(claim_id)
        return _as_dict(claim_id, self.claims[claim_id])

    # ----------------------------------------------------------------
    # checking it
    # ----------------------------------------------------------------

    @gl.public.write
    def check(self, claim_id: str) -> dict:
        """
        Fetch the document and record what consensus agreed it said.

        Callable by anyone, and that is the point: the URL, the token and the
        expected owner all come from storage, so there is nothing a caller
        can contribute that changes the answer. Anyone who doubts a proof can
        re-check it themselves.
        """
        key = claim_id.strip().lower()
        if key not in self.claims:
            raise gl.vm.UserError("no such claim: " + key)
        claim = self.claims[key]

        url = claim.url
        token = claim.token
        kind = claim.kind
        expect = claim.resource

        def leader_fn():
            return _probe(kind, url, token, expect)

        def validator_fn(leaders_res) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return False
            leader = leaders_res.calldata
            if not isinstance(leader, dict) or "status" not in leader:
                # A result of the wrong shape is a disagreement, not a crash.
                return False
            mine = _probe(kind, url, token, expect)

            # I could not fetch it. The only honest thing I can agree to is
            # that it could not be fetched.
            if mine["status"] == UNREACHABLE:
                return str(leader["status"]) == UNREACHABLE

            # I fetched it. Then it was not unreachable, whatever the leader
            # says: "down" and "the token is not there" are different claims.
            if str(leader["status"]) == UNREACHABLE:
                return False

            return str(leader["status"]) == mine["status"] and str(leader["note"]) == mine["note"]

        agreed = gl.vm.run_nondet(leader_fn, validator_fn)

        status = str(agreed["status"])
        if status not in (VERIFIED, MISSING, UNREACHABLE):
            raise gl.vm.UserError("unknown check outcome")

        claim.status = status
        claim.checked_at = _stamp()
        claim.note = str(agreed["note"])
        self.claims[key] = claim
        return _as_dict(key, claim)

    # ----------------------------------------------------------------
    # reading
    # ----------------------------------------------------------------

    @gl.public.view
    def get_claim(self, claim_id: str) -> dict:
        key = claim_id.strip().lower()
        if key not in self.claims:
            raise gl.vm.UserError("no such claim: " + key)
        return _as_dict(key, self.claims[key])

    @gl.public.view
    def is_verified(self, claim_id: str) -> bool:
        """
        The narrow question a consumer contract asks.

        Says nothing about how recently it was checked. A consumer that cares
        about staleness reads `checked_at` from `get_claim`, and LINK exists
        to make that somebody else's problem.
        """
        key = claim_id.strip().lower()
        if key not in self.claims:
            return False
        return self.claims[key].status == VERIFIED

    @gl.public.view
    def claim_list(self) -> list:
        return [c for c in self.claim_ids]

    @gl.public.view
    def count(self) -> int:
        return len(self.claim_ids)

    @gl.public.view
    def token_for_claim(self, owner: str, kind: str, resource: str) -> str:
        """
        What to publish, without opening a claim first.

        Free, so a claimant can prepare the file before paying for anything.
        """
        return token_for(_address(owner).as_hex, kind.strip(), resource.strip().lower())


# --------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------


def _stamp() -> str:
    """The transaction's own datetime, identical for every validator."""
    return str(gl.message.raw["datetime"])


def _address(value) -> Address:
    """Calldata turns address-shaped strings into Address; accept both."""
    if hasattr(value, "as_hex"):
        return value
    return Address(str(value).strip())


def _probe(kind: str, url: str, token: str, expect: str) -> dict:
    """
    Fetch once and say exactly what happened.

    Returns primitives only, so the whole result is comparable field by field
    between validators. `note` carries the reason, and it is compared too, so
    two validators cannot agree on an outcome while disagreeing about why.
    """
    import json

    try:
        resp = gl.nondet.web.get(url)
        status = int(resp.status)
        body = (resp.body or b"").decode("utf-8", errors="replace")
    except Exception:
        return {"status": UNREACHABLE, "note": "the document could not be fetched"}

    # 404 is the one failure that is an answer rather than an outage: the
    # path is fixed by this contract, so nothing is published there.
    if status == 404:
        return {"status": MISSING, "note": "nothing is published at that address"}
    if status < 200 or status >= 300:
        return {"status": UNREACHABLE, "note": "the host answered " + str(status)}

    if kind == KIND_DOMAIN:
        if token_present(body, token):
            return {"status": VERIFIED, "note": "the token is published at that domain"}
        return {"status": MISSING, "note": "the document does not carry the token"}

    # A handle claim has to clear both halves.
    try:
        document = json.loads(body)
    except Exception:
        return {"status": UNREACHABLE, "note": "the api did not answer json"}

    login = gist_owner(document)
    if not login:
        return {"status": MISSING, "note": "that gist names no owner"}
    if not same_handle(login, expect):
        return {"status": MISSING, "note": "that gist belongs to " + login.lower()}
    if not token_present(gist_text(document), token):
        return {"status": MISSING, "note": "the gist does not carry the token"}
    return {"status": VERIFIED, "note": "the token is published by that account"}


def _as_dict(key: str, claim: Claim) -> dict:
    return {
        "claim_id": key,
        "owner": claim.owner.as_hex,
        "kind": claim.kind,
        "resource": claim.resource,
        "locator": claim.locator,
        "token": claim.token,
        "url": claim.url,
        "opened_at": claim.opened_at,
        "status": claim.status,
        "checked_at": claim.checked_at,
        "note": claim.note,
    }
