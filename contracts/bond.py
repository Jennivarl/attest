# v0.3.0
# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }
"""
BOND. Putting money behind a claim, and letting anyone take it.

A proof says you controlled something when somebody last looked. That is
worth little on its own, because the cheapest attack on any directory is to
prove a resource, get listed, and quietly let the proof lapse while the
listing keeps vouching for you.

So a claim can be backed by a deposit, and anyone who believes the proof has
gone can put up a smaller stake and say so. Whoever is right takes the
other's money.

    post(link, binding_id)   stake behind your own published binding
    challenge(bond_id)       stake against it, which freezes the deposit
    resolve(bond_id)         settle it on a check taken after the challenge
    release(bond_id)         give notice that you want your deposit back
    collect(bond_id)         take it, once the notice has run unchallenged

Three outcomes, and the third is the one that makes this safe:

    the proof is missing      the challenger takes the deposit
    the proof is verified     the owner takes the challenger's stake
    the host is unreachable   nothing moves, and the challenge stays open

That third case is why PROOF refuses to collapse `missing` into
`unreachable`. If an outage counted as a withdrawal, taking anybody's
deposit would just mean waiting for their host to have a bad minute, and
the whole thing would be a lottery on uptime rather than a check on honesty.
"""

from dataclasses import dataclass

import genlayer as gl
from genlayer.types import *

from contracts.resource import MISSING, UNREACHABLE, VERIFIED, epoch_seconds

STATE_BONDED = "bonded"
STATE_CHALLENGED = "challenged"
STATE_LEAVING = "leaving"
STATE_SETTLED = "settled"
STATE_RELEASED = "released"

# A challenger stakes a fraction of the deposit. Enough that guessing is
# expensive, little enough that watching somebody honest is still worth it.
CHALLENGE_NUMERATOR = 1
CHALLENGE_DENOMINATOR = 10

# How long an owner must wait, unchallenged, before taking a deposit back.
# Without it an owner could pull the deposit in the same block somebody was
# preparing to challenge, and the bond would promise nothing.
NOTICE_SECONDS = 24 * 3600


@gl.storage.allow
@dataclass
class Bond:
    owner: Address
    amount: u256
    link: Address
    binding_id: str
    proof: Address
    claim_id: str
    resource: str
    posted_at: str
    state: str
    # Set while a challenge is open.
    challenger: Address
    challenge_stake: u256
    challenged_at: str
    # Set when the owner gives notice.
    notice_at: str
    # Filled in once it is over.
    decided_at: str
    outcome: str
    reason: str


class BondContract(gl.contract.Contract):
    bonds: gl.storage.TreeMap[str, Bond]
    bond_ids: gl.storage.DynArray[str]
    # What is owed to somebody: deposits plus live challenge stakes.
    committed: u256

    def __init__(self):
        self.committed = 0

    # ----------------------------------------------------------------
    # posting
    # ----------------------------------------------------------------

    @gl.public.write.payable
    def post(self, link: str, binding_id: str) -> dict:
        """
        Stake behind a binding you published.

        The binding has to be live and fresh right now. Staking behind a
        proof nobody has checked lately would let an owner post a deposit
        they could never lose.
        """
        amount = gl.message.value
        if amount <= 0:
            raise gl.vm.UserError("send the deposit with this call")
        if amount < CHALLENGE_DENOMINATOR:
            # Otherwise the challenger's share rounds to nothing and the
            # bond cannot be challenged at all.
            raise gl.vm.UserError("that deposit is too small to be challenged")

        at = _address(link)
        key = binding_id.strip().lower()
        binding = gl.contract.get_at(at).view().get_binding(key)

        owner = _address(binding["owner"])
        if owner.as_hex.lower() != gl.message.sender_address.as_hex.lower():
            raise gl.vm.UserError("only the address that published it may stake behind it")
        if not bool(binding["live"]):
            raise gl.vm.UserError("that binding is not live")
        if not bool(binding["fresh"]):
            raise gl.vm.UserError("that binding is not fresh; check the proof again first")

        bond_id = owner.as_hex.lower() + "|" + key
        if bond_id in self.bonds and self.bonds[bond_id].state in (
            STATE_BONDED,
            STATE_CHALLENGED,
            STATE_LEAVING,
        ):
            raise gl.vm.UserError("that binding is already bonded")

        first_time = bond_id not in self.bonds
        self.bonds[bond_id] = Bond(
            owner=owner,
            amount=amount,
            link=at,
            binding_id=key,
            proof=_address(binding["proof"]),
            claim_id=str(binding["claim_id"]),
            resource=str(binding["kind"]) + ":" + str(binding["resource"]),
            posted_at=_stamp(),
            state=STATE_BONDED,
            challenger=_zero(),
            challenge_stake=0,
            challenged_at="",
            notice_at="",
            decided_at="",
            outcome="",
            reason="",
        )
        if first_time:
            self.bond_ids.append(bond_id)
        self.committed = self.committed + amount
        return _as_dict(bond_id, self.bonds[bond_id])

    # ----------------------------------------------------------------
    # challenging
    # ----------------------------------------------------------------

    @gl.public.write.payable
    def challenge(self, bond_id: str) -> dict:
        """
        Say the proof has gone, and back it with money.

        A challenge does not decide anything by itself. It freezes the
        deposit and starts the clock; the answer comes from a check taken
        after this moment, which anybody can trigger on PROOF.
        """
        key = bond_id.strip().lower()
        bond = self._live(key)
        if bond.state == STATE_CHALLENGED:
            raise gl.vm.UserError("that bond is already challenged")

        stake = gl.message.value
        wanted = int(bond.amount) * CHALLENGE_NUMERATOR // CHALLENGE_DENOMINATOR
        if stake < wanted:
            raise gl.vm.UserError(f"a challenge stakes at least {wanted}")
        if gl.message.sender_address.as_hex.lower() == bond.owner.as_hex.lower():
            # Otherwise an owner could challenge themselves to dodge the
            # notice period and get their deposit straight back.
            raise gl.vm.UserError("an owner cannot challenge their own bond")

        bond.state = STATE_CHALLENGED
        bond.challenger = gl.message.sender_address
        bond.challenge_stake = stake
        bond.challenged_at = _stamp()
        bond.reason = "waiting for a check taken after the challenge"
        self.bonds[key] = bond
        self.committed = self.committed + stake
        return _as_dict(key, bond)

    @gl.public.write
    def resolve(self, bond_id: str) -> dict:
        """
        Settle a challenge on evidence, not on who shouted.

        Reads PROOF for the claim behind the bond and requires the check to
        be newer than the challenge, so neither side can win on a reading
        that was already sitting there when they acted.
        """
        key = bond_id.strip().lower()
        if key not in self.bonds:
            raise gl.vm.UserError("no such bond: " + key)
        bond = self.bonds[key]
        if bond.state != STATE_CHALLENGED:
            raise gl.vm.UserError("that bond is not challenged: " + bond.state)

        record = gl.contract.get_at(bond.proof).view().get_claim(bond.claim_id)
        status = str(record["status"])

        checked = epoch_seconds(str(record["checked_at"]))
        opened = epoch_seconds(bond.challenged_at)
        if checked is None or opened is None:
            return self._waiting(key, bond, "a timestamp on this bond or check could not be read")
        if checked <= opened:
            return self._waiting(key, bond, "no check has been taken since the challenge")

        if status == UNREACHABLE:
            # Nobody knows, so nobody wins. The challenge stays open and the
            # money stays where it is until somebody can actually reach it.
            return self._waiting(key, bond, "the host was unreachable, so nothing is decided")

        if status == MISSING:
            winner = bond.challenger
            outcome = "challenger"
            reason = "the proof was gone: " + str(record["note"])
        elif status == VERIFIED:
            winner = bond.owner
            outcome = "owner"
            reason = "the proof still held: " + str(record["note"])
        else:
            return self._waiting(key, bond, "the claim has never been checked")

        total = int(bond.amount) + int(bond.challenge_stake)
        bond.state = STATE_SETTLED
        bond.outcome = outcome
        bond.reason = reason
        bond.decided_at = _stamp()
        self.bonds[key] = bond
        self.committed = self.committed - total
        # State first, then value.
        _pay(winner, total)
        return _as_dict(key, bond)

    # ----------------------------------------------------------------
    # leaving
    # ----------------------------------------------------------------

    @gl.public.write
    def release(self, bond_id: str) -> dict:
        """Give notice that you want the deposit back. Owner only."""
        key = bond_id.strip().lower()
        bond = self._live(key)
        if bond.state == STATE_CHALLENGED:
            raise gl.vm.UserError("that bond is challenged; it has to be resolved first")
        if bond.state == STATE_LEAVING:
            raise gl.vm.UserError("notice has already been given")
        if gl.message.sender_address.as_hex.lower() != bond.owner.as_hex.lower():
            raise gl.vm.UserError("only the owner may give notice")

        bond.state = STATE_LEAVING
        bond.notice_at = _stamp()
        bond.reason = f"notice given; collectable in {NOTICE_SECONDS} seconds if unchallenged"
        self.bonds[key] = bond
        return _as_dict(key, bond)

    @gl.public.write
    def collect(self, bond_id: str) -> dict:
        """
        Take the deposit back, once the notice has run and nobody objected.

        Still challengeable right up to the last second, which is the point
        of the notice: an owner cannot escape a challenge by being quick.
        """
        key = bond_id.strip().lower()
        if key not in self.bonds:
            raise gl.vm.UserError("no such bond: " + key)
        bond = self.bonds[key]
        if bond.state != STATE_LEAVING:
            raise gl.vm.UserError("no notice has been given on that bond: " + bond.state)

        given = epoch_seconds(bond.notice_at)
        now = epoch_seconds(_stamp())
        if given is None or now is None:
            raise gl.vm.UserError("a timestamp on this bond could not be read")
        waited = now - given
        if waited < NOTICE_SECONDS:
            raise gl.vm.UserError(
                f"the notice has run {waited} seconds and must run {NOTICE_SECONDS}"
            )

        amount = int(bond.amount)
        bond.state = STATE_RELEASED
        bond.outcome = "owner"
        bond.reason = "the notice ran unchallenged"
        bond.decided_at = _stamp()
        self.bonds[key] = bond
        self.committed = self.committed - amount
        _pay(bond.owner, amount)
        return _as_dict(key, bond)

    # ----------------------------------------------------------------
    # reading
    # ----------------------------------------------------------------

    @gl.public.view
    def get_bond(self, bond_id: str) -> dict:
        key = bond_id.strip().lower()
        if key not in self.bonds:
            raise gl.vm.UserError("no such bond: " + key)
        return _as_dict(key, self.bonds[key])

    @gl.public.view
    def backing(self, resource: str) -> str:
        """How much is currently staked behind a resource, as a number."""
        want = resource.strip().lower()
        total = 0
        for bond_id in self.bond_ids:
            bond = self.bonds[bond_id]
            if bond.resource == want and bond.state in (STATE_BONDED, STATE_CHALLENGED, STATE_LEAVING):
                total = total + int(bond.amount)
        return str(total)

    @gl.public.view
    def bond_list(self) -> list:
        return [b for b in self.bond_ids]

    @gl.public.view
    def reserves(self) -> dict:
        return {"committed": str(self.committed)}

    @gl.public.view
    def terms(self) -> dict:
        return {
            "challenge_fraction": f"{CHALLENGE_NUMERATOR}/{CHALLENGE_DENOMINATOR}",
            "notice_seconds": str(NOTICE_SECONDS),
        }

    # ----------------------------------------------------------------
    # internals
    # ----------------------------------------------------------------

    def _live(self, key: str) -> Bond:
        if key not in self.bonds:
            raise gl.vm.UserError("no such bond: " + key)
        bond = self.bonds[key]
        if bond.state in (STATE_SETTLED, STATE_RELEASED):
            raise gl.vm.UserError("that bond is already over: " + bond.state)
        return bond

    def _waiting(self, key: str, bond: Bond, why: str) -> dict:
        """Record why nothing was decided, and leave the challenge open."""
        bond.reason = why
        self.bonds[key] = bond
        return _as_dict(key, bond)


# --------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------


def _stamp() -> str:
    return str(gl.message.raw["datetime"])


def _zero() -> Address:
    return Address("0x" + "00" * 20)


def _address(value) -> Address:
    if hasattr(value, "as_hex"):
        return value
    return Address(str(value).strip())


@gl.evm.contract_interface
class _Wallet:
    """A plain account on the EVM side: nothing to call, only to pay."""

    class View:
        pass

    class Write:
        pass


def _pay(to: Address, amount: int) -> None:
    """
    The one place value leaves this contract.

    Through the EVM interface, which is what actually reaches a plain
    wallet. The transaction must also reserve a fee for that outgoing
    message, which the client does by declaring a funded allocation; see
    deploy/chain.mjs.
    """
    _Wallet(to).emit_transfer(value=amount)


def _as_dict(key: str, bond: Bond) -> dict:
    return {
        "bond_id": key,
        "owner": bond.owner.as_hex,
        "amount": str(bond.amount),
        "link": bond.link.as_hex,
        "binding_id": bond.binding_id,
        "proof": bond.proof.as_hex,
        "claim_id": bond.claim_id,
        "resource": bond.resource,
        "posted_at": bond.posted_at,
        "state": bond.state,
        "challenger": bond.challenger.as_hex,
        "challenge_stake": str(bond.challenge_stake),
        "challenged_at": bond.challenged_at,
        "notice_at": bond.notice_at,
        "decided_at": bond.decided_at,
        "outcome": bond.outcome,
        "reason": bond.reason,
    }
