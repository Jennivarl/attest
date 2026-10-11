# v0.3.0
# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }
"""
LINK. The directory: who controls what, and how long ago anyone checked.

PROOF answers "did this address prove this resource". That is the wrong way
round for almost every consumer, who starts from the resource and wants to
know whose address is behind it. LINK answers that, and adds the two things
a directory needs that a prover does not:

    freshness    a proof is a statement about a moment, and this one says
                 how old that moment is rather than pretending it is now
    withdrawal   the owner can take a binding down, and a consumer can see
                 that it went

    adopt(claim_id)      publish a verified claim in the directory
    resolve(resource)    who controls it, and how fresh each answer is
    withdraw(claim_id)   take your own binding down
    holdings(address)    everything one address has proven

LINK never fetches anything. It reads PROOF's stored record, which consensus
already agreed on, and refuses to adopt anything that is not verified. That
keeps the network work in one contract and makes this one cheap to call.

A resource can be bound by more than one address at once, and LINK reports
that rather than hiding it. Two people really can both control a domain, and
silently picking a winner would be the directory inventing a fact. A
consumer that cannot tolerate ambiguity checks `contested` and refuses.
"""

from dataclasses import dataclass

import genlayer as gl
from genlayer.types import *

from contracts.resource import MISSING, VERIFIED, epoch_seconds

# How long a proof stays fresh. Not a deadline and nothing expires on its
# own: this is the line LINK draws when reporting, so a consumer does not
# have to invent its own. Thirty days is long enough that renewing is not a
# chore and short enough that an abandoned domain stops vouching for anyone.
FRESH_SECONDS = 30 * 24 * 3600


@gl.storage.allow
@dataclass
class Binding:
    owner: Address
    kind: str
    resource: str
    claim_id: str
    proof: Address
    # The moment PROOF last agreed this was verified.
    verified_at: str
    adopted_at: str
    live: bool
    withdrawn_at: str


class Link(gl.contract.Contract):
    # The one PROOF this directory believes. Fixed when the directory is
    # deployed and never again, because a caller who can name the contract
    # that vouches for them can deploy one that vouches for anybody.
    proof: Address
    bindings: gl.storage.TreeMap[str, Binding]
    # Every binding ever published. The lookups below filter this rather
    # than keeping index maps: a nested collection inside a TreeMap is a
    # sharper tool than this needs, and the views that read it are free.
    binding_ids: gl.storage.DynArray[str]

    def __init__(self, proof: str):
        self.proof = _address(proof)

    # ----------------------------------------------------------------
    # publishing
    # ----------------------------------------------------------------

    @gl.public.write
    def adopt(self, claim_id: str) -> dict:
        """
        Publish a verified claim in the directory.

        Only the claim's own owner may do this, even though the proof itself
        is public. Being able to see that somebody proved a domain is not the
        same as being entitled to list them, and a directory anyone can write
        to on somebody else's behalf is a directory of other people's
        business.

        The claim is read from the PROOF this directory was deployed
        against, never from one the caller names.
        """
        at = self.proof
        key = claim_id.strip().lower()

        record = gl.contract.get_at(at).view().get_claim(key)
        if str(record["status"]) != VERIFIED:
            raise gl.vm.UserError("that claim is not verified: " + str(record["status"] or "never checked"))

        owner = _address(record["owner"])
        if owner.as_hex.lower() != gl.message.sender_address.as_hex.lower():
            raise gl.vm.UserError("only the address that proved it may publish it")

        binding_id = at.as_hex.lower() + "|" + key
        if binding_id in self.bindings and self.bindings[binding_id].live:
            raise gl.vm.UserError("that binding is already published")

        fresh = Binding(
            owner=owner,
            kind=str(record["kind"]),
            resource=str(record["resource"]),
            claim_id=key,
            proof=at,
            verified_at=str(record["checked_at"]),
            adopted_at=_stamp(),
            live=True,
            withdrawn_at="",
        )
        first_time = binding_id not in self.bindings
        self.bindings[binding_id] = fresh
        if first_time:
            self.binding_ids.append(binding_id)
        return _as_dict(binding_id, fresh, _now())

    @gl.public.write
    def refresh(self, binding_id: str) -> dict:
        """
        Pull the latest check across from PROOF.

        Anyone may call it, because it can only copy what consensus already
        agreed. If the proof has since gone missing, this is how the
        directory finds out, and the binding stops being live.
        """
        key = binding_id.strip().lower()
        if key not in self.bindings:
            raise gl.vm.UserError("no such binding: " + key)
        binding = self.bindings[key]

        record = gl.contract.get_at(binding.proof).view().get_claim(binding.claim_id)
        status = str(record["status"])
        if status == VERIFIED:
            binding.verified_at = str(record["checked_at"])
            binding.live = True
            binding.withdrawn_at = ""
        elif status == MISSING:
            # Only a real `missing` takes a binding down. An unreachable
            # check means nobody knows, and nobody knowing is not grounds
            # for stripping somebody of a proof they may still hold.
            binding.live = False
            binding.withdrawn_at = _stamp()
        self.bindings[key] = binding
        return _as_dict(key, binding, _now())

    @gl.public.write
    def withdraw(self, binding_id: str) -> dict:
        """Take your own binding down. Only the owner, and it is not deleted."""
        key = binding_id.strip().lower()
        if key not in self.bindings:
            raise gl.vm.UserError("no such binding: " + key)
        binding = self.bindings[key]
        if binding.owner.as_hex.lower() != gl.message.sender_address.as_hex.lower():
            raise gl.vm.UserError("only the owner may withdraw a binding")
        if not binding.live:
            raise gl.vm.UserError("that binding is already down")
        binding.live = False
        binding.withdrawn_at = _stamp()
        self.bindings[key] = binding
        return _as_dict(key, binding, _now())

    # ----------------------------------------------------------------
    # reading
    # ----------------------------------------------------------------

    @gl.public.view
    def resolve(self, kind: str, resource: str) -> dict:
        """
        Who controls this resource, and how sure can anyone be.

        Returns every live binding rather than one winner. `contested` is
        true when more than one address holds the resource at once, which is
        a real situation and not an error: a consumer that cannot live with
        it should refuse rather than be handed an arbitrary pick.
        """
        want_kind = kind.strip().lower()
        want_resource = resource.strip().lower()
        now = _now()
        found = []
        for binding_id in self.binding_ids:
            binding = self.bindings[binding_id]
            if binding.live and binding.kind == want_kind and binding.resource == want_resource:
                found.append(_as_dict(binding_id, binding, now))
        return {
            "kind": kind.strip().lower(),
            "resource": resource.strip().lower(),
            "bindings": found,
            "contested": len(found) > 1,
            "fresh": [b for b in found if b["fresh"]],
        }

    @gl.public.view
    def controls(self, kind: str, resource: str, address: str) -> bool:
        """
        The narrow question, and the one a gate should ask: does this address
        hold a live and fresh binding for this resource, right now.
        """
        who = _address(address).as_hex.lower()
        for binding in self.resolve(kind, resource)["fresh"]:
            if str(binding["owner"]).lower() == who:
                return True
        return False

    @gl.public.view
    def holdings(self, address: str) -> list:
        """Everything one address has published, live or not."""
        who = _address(address).as_hex.lower()
        now = _now()
        return [
            _as_dict(b, self.bindings[b], now)
            for b in self.binding_ids
            if self.bindings[b].owner.as_hex.lower() == who
        ]

    @gl.public.view
    def get_binding(self, binding_id: str) -> dict:
        key = binding_id.strip().lower()
        if key not in self.bindings:
            raise gl.vm.UserError("no such binding: " + key)
        return _as_dict(key, self.bindings[key], _now())

    @gl.public.view
    def settings(self) -> dict:
        """Which PROOF this directory believes, so a consumer can check."""
        return {"proof": self.proof.as_hex, "fresh_for_seconds": str(FRESH_SECONDS)}

    @gl.public.view
    def binding_list(self) -> list:
        return [b for b in self.binding_ids]

    @gl.public.view
    def count(self) -> int:
        return len(self.binding_ids)


# --------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------


def _stamp() -> str:
    return str(gl.message.raw["datetime"])


def _now():
    return epoch_seconds(_stamp())


def _address(value) -> Address:
    if hasattr(value, "as_hex"):
        return value
    return Address(str(value).strip())


def _as_dict(key: str, binding: Binding, now) -> dict:
    """
    One binding, with its age worked out.

    `fresh` is false whenever the age cannot be established, not just when
    it is too old: an unreadable timestamp is a reason to doubt a binding,
    never a reason to trust it.
    """
    seen = epoch_seconds(binding.verified_at)
    age = -1
    fresh = False
    if seen is not None and now is not None and now >= seen:
        age = now - seen
        fresh = binding.live and age <= FRESH_SECONDS
    return {
        "binding_id": key,
        "owner": binding.owner.as_hex,
        "kind": binding.kind,
        "resource": binding.resource,
        "claim_id": binding.claim_id,
        "proof": binding.proof.as_hex,
        "verified_at": binding.verified_at,
        "adopted_at": binding.adopted_at,
        "live": binding.live,
        "withdrawn_at": binding.withdrawn_at,
        "age_seconds": str(age),
        "fresh": fresh,
        "fresh_for_seconds": str(FRESH_SECONDS),
    }
