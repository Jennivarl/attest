"""
LINK run for real in direct mode, against a stand-in PROOF.

The directory never touches the network. Everything here is about what it
is willing to publish, who is allowed to publish it, and whether it tells
the truth about how old a proof is.
"""

import sys
from pathlib import Path

import pytest

from test.stub_proof import CHECKED, CLAIM, DOMAIN, OWNER, STRANGER, ProofStub

BUNDLE = str(Path(__file__).resolve().parent.parent / "contracts" / "link_bundle.py")

PROOF_AT = "0x" + ("dd" * 20)
BINDING = PROOF_AT.lower() + "|" + CLAIM

NOW = "2026-10-10T09:00:00Z"          # a week after the proof was checked
MUCH_LATER = "2026-12-01T09:00:00Z"   # well past thirty days
FRESH_SECONDS = 30 * 24 * 3600


def set_date(vm, stamp: str) -> None:
    vm.warp(stamp)
    message = sys.modules.get("genlayer.message")
    if message is not None and isinstance(getattr(message, "raw", None), dict):
        message.raw["datetime"] = stamp
    gl = sys.modules.get("genlayer.gl")
    if gl is not None and getattr(gl, "message_raw", None) is not None:
        gl.message_raw["datetime"] = stamp


@pytest.fixture
def proof(direct_vm):
    return ProofStub().install(direct_vm)


@pytest.fixture
def link(direct_vm, direct_deploy, proof):
    contract = direct_deploy(BUNDLE)
    direct_vm.strict_mocks = False
    set_date(direct_vm, NOW)
    return contract


def adopt(vm, link, who=OWNER):
    with vm.prank(who):
        return link.adopt(PROOF_AT, CLAIM)


# --------------------------------------------------------------------
# publishing
# --------------------------------------------------------------------


def test_a_verified_claim_can_be_published_by_its_owner(direct_vm, link):
    got = adopt(direct_vm, link)

    assert got["resource"] == DOMAIN
    assert got["owner"].lower() == "0x" + OWNER.hex()
    assert got["live"] is True
    assert got["verified_at"] == CHECKED
    assert link.count() == 1


def test_nobody_can_publish_somebody_elses_proof(direct_vm, link):
    """
    Seeing that an address proved a domain is not the same as being entitled
    to list them in a directory.
    """
    with direct_vm.expect_revert("only the address that proved it"):
        adopt(direct_vm, link, who=STRANGER)


@pytest.mark.parametrize("status", ["missing", "unreachable", ""])
def test_a_claim_that_is_not_verified_cannot_be_published(direct_vm, link, proof, status):
    proof.set_claim(status=status)
    with direct_vm.expect_revert("not verified"):
        adopt(direct_vm, link)


def test_publishing_the_same_binding_twice_is_refused(direct_vm, link):
    adopt(direct_vm, link)
    with direct_vm.expect_revert("already published"):
        adopt(direct_vm, link)


# --------------------------------------------------------------------
# looking it up
# --------------------------------------------------------------------


def test_the_directory_answers_from_the_resource_not_the_address(direct_vm, link):
    """The whole reason this contract exists: PROOF cannot answer this."""
    adopt(direct_vm, link)
    got = link.resolve("domain", DOMAIN)

    assert len(got["bindings"]) == 1
    assert got["bindings"][0]["owner"].lower() == "0x" + OWNER.hex()
    assert got["contested"] is False


def test_an_unknown_resource_resolves_to_nothing_rather_than_failing(link):
    got = link.resolve("domain", "nobody-owns-this.example")
    assert got["bindings"] == []
    assert got["contested"] is False


def test_two_addresses_proving_one_resource_are_both_reported(direct_vm, link, proof):
    """
    Two people really can both control a domain. Picking a winner silently
    would be the directory inventing a fact, so it says so instead.
    """
    adopt(direct_vm, link)
    other_claim = "0x" + STRANGER.hex() + ":domain:" + DOMAIN
    proof.set_claim(claim_id=other_claim, owner="0x" + STRANGER.hex())
    with direct_vm.prank(STRANGER):
        link.adopt(PROOF_AT, other_claim)

    got = link.resolve("domain", DOMAIN)
    assert len(got["bindings"]) == 2
    assert got["contested"] is True


def test_controls_is_the_narrow_question_a_gate_should_ask(direct_vm, link):
    adopt(direct_vm, link)
    assert link.controls("domain", DOMAIN, "0x" + OWNER.hex()) is True
    assert link.controls("domain", DOMAIN, "0x" + STRANGER.hex()) is False
    assert link.controls("domain", "other.example", "0x" + OWNER.hex()) is False


def test_holdings_lists_everything_one_address_has_proven(direct_vm, link):
    adopt(direct_vm, link)
    assert len(link.holdings("0x" + OWNER.hex())) == 1
    assert link.holdings("0x" + STRANGER.hex()) == []


# --------------------------------------------------------------------
# freshness, which is the honest part
# --------------------------------------------------------------------


def test_a_recent_proof_is_fresh_and_says_how_old_it_is(direct_vm, link):
    got = adopt(direct_vm, link)
    assert got["fresh"] is True
    assert got["age_seconds"] == str(7 * 24 * 3600)


def test_an_old_proof_is_still_live_but_no_longer_fresh(direct_vm, link):
    """
    Nothing expires on its own. The directory keeps reporting the binding
    and stops vouching for it, which is the honest thing to do with a
    statement about a moment that has passed.
    """
    adopt(direct_vm, link)
    set_date(direct_vm, MUCH_LATER)

    got = link.resolve("domain", DOMAIN)
    assert got["bindings"][0]["live"] is True
    assert got["bindings"][0]["fresh"] is False
    assert got["fresh"] == []
    assert link.controls("domain", DOMAIN, "0x" + OWNER.hex()) is False


def test_the_freshness_window_is_published_rather_than_hidden(direct_vm, link):
    got = adopt(direct_vm, link)
    assert got["fresh_for_seconds"] == str(FRESH_SECONDS)


def test_a_timestamp_that_cannot_be_read_is_never_treated_as_fresh(direct_vm, link, proof):
    """Doubt about a binding's age is a reason to distrust it, not to trust it."""
    proof.set_claim(checked_at="not a timestamp")
    got = adopt(direct_vm, link)
    assert got["fresh"] is False
    assert got["age_seconds"] == "-1"


# --------------------------------------------------------------------
# taking it down
# --------------------------------------------------------------------


def test_an_owner_can_withdraw_their_own_binding(direct_vm, link):
    adopt(direct_vm, link)
    with direct_vm.prank(OWNER):
        got = link.withdraw(BINDING)

    assert got["live"] is False
    assert got["withdrawn_at"] == NOW
    assert link.resolve("domain", DOMAIN)["bindings"] == []


def test_nobody_else_can_withdraw_it(direct_vm, link):
    adopt(direct_vm, link)
    with direct_vm.prank(STRANGER):
        with direct_vm.expect_revert("only the owner"):
            link.withdraw(BINDING)


def test_a_withdrawn_binding_is_kept_rather_than_deleted(direct_vm, link):
    """A consumer that saw it yesterday should be able to see that it went."""
    adopt(direct_vm, link)
    with direct_vm.prank(OWNER):
        link.withdraw(BINDING)

    got = link.get_binding(BINDING)
    assert got["live"] is False
    assert link.count() == 1


def test_refresh_takes_a_binding_down_when_the_proof_has_gone(direct_vm, link, proof):
    adopt(direct_vm, link)
    proof.set_claim(status="missing", checked_at=NOW)

    got = link.refresh(BINDING)
    assert got["live"] is False
    assert link.resolve("domain", DOMAIN)["bindings"] == []


def test_refresh_does_not_take_a_binding_down_merely_because_a_host_was_down(direct_vm, link, proof):
    """
    The distinction PROOF keeps, honoured here. An outage is not a
    withdrawal, and treating it as one would let anyone strip a proof by
    waiting for a bad minute.
    """
    adopt(direct_vm, link)
    proof.set_claim(status="unreachable", checked_at=NOW)

    got = link.refresh(BINDING)
    assert got["live"] is True
    assert link.controls("domain", DOMAIN, "0x" + OWNER.hex()) is True


def test_refresh_brings_a_binding_back_when_the_proof_returns(direct_vm, link, proof):
    adopt(direct_vm, link)
    proof.set_claim(status="missing", checked_at=NOW)
    link.refresh(BINDING)
    assert link.get_binding(BINDING)["live"] is False

    proof.set_claim(status="verified", checked_at=NOW)
    got = link.refresh(BINDING)
    assert got["live"] is True
    assert got["verified_at"] == NOW


def test_anyone_may_refresh_because_it_only_copies_what_consensus_agreed(direct_vm, link, proof):
    adopt(direct_vm, link)
    proof.set_claim(status="missing", checked_at=NOW)
    with direct_vm.prank(STRANGER):
        assert link.refresh(BINDING)["live"] is False
