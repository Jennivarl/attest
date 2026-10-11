"""
BOND run for real in direct mode, against stand-ins for LINK and PROOF.

Every test here is about money: who can stake it, who can take it, when, and
what happens when nobody can tell. The case that matters most is the one
where nothing happens, because a bond that could be taken during an outage
would be a lottery on uptime rather than a check on honesty.
"""

import sys
from pathlib import Path

import pytest

from test.stub_proof import CHECKED, CLAIM, DOMAIN, EVM_PAY, OWNER, STRANGER, ProofStub

BUNDLE = str(Path(__file__).resolve().parent.parent / "contracts" / "bond_bundle.py")

LINK_AT = "0x" + ("cc" * 20)
BINDING = "0x" + "dd" * 20 + "|" + CLAIM
BOND_ID = "0x" + OWNER.hex() + "|" + BINDING.lower()

DEPOSIT = 10_000
STAKE = DEPOSIT // 10
NOTICE_SECONDS = 24 * 3600

NOW = "2026-10-10T09:00:00Z"
AFTER_CHALLENGE = "2026-10-10T10:00:00Z"
PAST_NOTICE = "2026-10-11T09:00:01Z"


def set_date(vm, stamp: str) -> None:
    vm.warp(stamp)
    message = sys.modules.get("genlayer.message")
    if message is not None and isinstance(getattr(message, "raw", None), dict):
        message.raw["datetime"] = stamp
    gl = sys.modules.get("genlayer.gl")
    if gl is not None and getattr(gl, "message_raw", None) is not None:
        gl.message_raw["datetime"] = stamp


@pytest.fixture
def chain(direct_vm):
    return ProofStub().install(direct_vm)


@pytest.fixture
def bond(direct_vm, direct_deploy, chain):
    contract = direct_deploy(BUNDLE, link=LINK_AT)
    direct_vm.strict_mocks = False
    set_date(direct_vm, NOW)
    return contract


def post(vm, bond, who=OWNER, amount=DEPOSIT):
    vm.value = amount
    try:
        with vm.prank(who):
            return bond.post(BINDING)
    finally:
        vm.value = 0


def challenge(vm, bond, who=STRANGER, amount=STAKE):
    vm.value = amount
    try:
        with vm.prank(who):
            return bond.challenge(BOND_ID)
    finally:
        vm.value = 0


# --------------------------------------------------------------------
# posting
# --------------------------------------------------------------------


def test_an_owner_can_stake_behind_their_own_live_binding(direct_vm, bond):
    got = post(direct_vm, bond)

    assert got["state"] == "bonded"
    assert got["amount"] == str(DEPOSIT)
    assert got["resource"] == "domain:" + DOMAIN
    assert bond.reserves()["committed"] == str(DEPOSIT)
    assert bond.backing("domain:" + DOMAIN) == str(DEPOSIT)


def test_nobody_can_stake_behind_somebody_elses_binding(direct_vm, bond):
    with direct_vm.expect_revert("only the address that published it"):
        post(direct_vm, bond, who=STRANGER)


def test_a_bond_with_no_deposit_is_refused(direct_vm, bond):
    with direct_vm.expect_revert("send the deposit"):
        post(direct_vm, bond, amount=0)


def test_a_deposit_too_small_to_be_challenged_is_refused(direct_vm, bond):
    """Otherwise the challenger's share rounds to nothing and nobody can object."""
    with direct_vm.expect_revert("too small to be challenged"):
        post(direct_vm, bond, amount=5)


def test_a_binding_that_is_not_live_cannot_be_bonded(direct_vm, bond, chain):
    chain.set_binding(live=False)
    with direct_vm.expect_revert("not live"):
        post(direct_vm, bond)


def test_a_stale_binding_cannot_be_bonded(direct_vm, bond, chain):
    """Staking behind a proof nobody has checked lately risks nothing."""
    chain.set_binding(fresh=False)
    with direct_vm.expect_revert("not fresh"):
        post(direct_vm, bond)


def test_the_same_binding_cannot_be_bonded_twice(direct_vm, bond):
    post(direct_vm, bond)
    with direct_vm.expect_revert("already bonded"):
        post(direct_vm, bond)


# --------------------------------------------------------------------
# challenging
# --------------------------------------------------------------------


def test_anyone_can_challenge_by_staking_a_tenth(direct_vm, bond):
    post(direct_vm, bond)
    got = challenge(direct_vm, bond)

    assert got["state"] == "challenged"
    assert got["challenge_stake"] == str(STAKE)
    assert got["challenger"].lower() == "0x" + STRANGER.hex()
    assert bond.reserves()["committed"] == str(DEPOSIT + STAKE)


def test_a_challenge_that_stakes_too_little_is_refused(direct_vm, bond):
    post(direct_vm, bond)
    with direct_vm.expect_revert("stakes at least"):
        challenge(direct_vm, bond, amount=STAKE - 1)


def test_an_owner_cannot_challenge_their_own_bond(direct_vm, bond):
    """It would be a way to dodge the notice period and exit instantly."""
    post(direct_vm, bond)
    with direct_vm.expect_revert("cannot challenge their own"):
        challenge(direct_vm, bond, who=OWNER)


def test_a_bond_cannot_be_challenged_twice_at_once(direct_vm, bond):
    post(direct_vm, bond)
    challenge(direct_vm, bond)
    with direct_vm.expect_revert("already challenged"):
        challenge(direct_vm, bond)


# --------------------------------------------------------------------
# resolving, which is where the money moves
# --------------------------------------------------------------------


def test_a_proof_that_has_gone_pays_the_challenger(direct_vm, bond, chain):
    post(direct_vm, bond)
    challenge(direct_vm, bond)
    chain.set_claim(status="missing", checked_at=AFTER_CHALLENGE, note="the document does not carry the token")
    set_date(direct_vm, AFTER_CHALLENGE)

    got = bond.resolve(BOND_ID)
    assert got["state"] == "settled"
    assert got["outcome"] == "challenger"
    assert chain.transfers == [{"to": STRANGER, "value": DEPOSIT + STAKE, "kind": EVM_PAY}]
    assert bond.reserves()["committed"] == "0"


def test_a_proof_that_still_holds_pays_the_owner(direct_vm, bond, chain):
    post(direct_vm, bond)
    challenge(direct_vm, bond)
    chain.set_claim(status="verified", checked_at=AFTER_CHALLENGE)
    set_date(direct_vm, AFTER_CHALLENGE)

    got = bond.resolve(BOND_ID)
    assert got["state"] == "settled"
    assert got["outcome"] == "owner"
    assert chain.transfers == [{"to": OWNER, "value": DEPOSIT + STAKE, "kind": EVM_PAY}]


def test_an_unreachable_host_decides_nothing_and_moves_nothing(direct_vm, bond, chain):
    """
    The case the three statuses exist for. If an outage counted as a
    withdrawal, taking anybody's deposit would mean waiting for a bad minute.
    """
    post(direct_vm, bond)
    challenge(direct_vm, bond)
    chain.set_claim(status="unreachable", checked_at=AFTER_CHALLENGE, note="the host answered 503")
    set_date(direct_vm, AFTER_CHALLENGE)

    got = bond.resolve(BOND_ID)
    assert got["state"] == "challenged"
    assert "unreachable" in got["reason"]
    assert chain.transfers == []
    assert bond.reserves()["committed"] == str(DEPOSIT + STAKE)


def test_an_unreachable_host_can_be_resolved_later_once_it_answers(direct_vm, bond, chain):
    """Waiting is a delay, never a verdict. The challenge stays open."""
    post(direct_vm, bond)
    challenge(direct_vm, bond)
    chain.set_claim(status="unreachable", checked_at=AFTER_CHALLENGE)
    set_date(direct_vm, AFTER_CHALLENGE)
    assert bond.resolve(BOND_ID)["state"] == "challenged"

    chain.set_claim(status="missing", checked_at="2026-10-10T12:00:00Z")
    set_date(direct_vm, "2026-10-10T12:00:00Z")
    assert bond.resolve(BOND_ID)["state"] == "settled"


def test_a_check_older_than_the_challenge_decides_nothing(direct_vm, bond, chain):
    """
    Neither side may win on a reading that was already sitting there when
    they acted. The same staleness rule the rest of this work uses.
    """
    post(direct_vm, bond)
    challenge(direct_vm, bond)
    chain.set_claim(status="missing", checked_at=CHECKED)  # before the challenge

    got = bond.resolve(BOND_ID)
    assert got["state"] == "challenged"
    assert "since the challenge" in got["reason"]
    assert chain.transfers == []


def test_a_bond_that_was_never_challenged_cannot_be_resolved(direct_vm, bond):
    post(direct_vm, bond)
    with direct_vm.expect_revert("not challenged"):
        bond.resolve(BOND_ID)


def test_a_settled_bond_cannot_be_settled_again(direct_vm, bond, chain):
    post(direct_vm, bond)
    challenge(direct_vm, bond)
    chain.set_claim(status="missing", checked_at=AFTER_CHALLENGE)
    set_date(direct_vm, AFTER_CHALLENGE)
    bond.resolve(BOND_ID)

    with direct_vm.expect_revert("not challenged"):
        bond.resolve(BOND_ID)
    assert len(chain.transfers) == 1


def test_a_timestamp_that_cannot_be_read_decides_nothing(direct_vm, bond, chain):
    post(direct_vm, bond)
    challenge(direct_vm, bond)
    chain.set_claim(status="missing", checked_at="not a timestamp")

    got = bond.resolve(BOND_ID)
    assert got["state"] == "challenged"
    assert chain.transfers == []


# --------------------------------------------------------------------
# leaving
# --------------------------------------------------------------------


def test_an_owner_gives_notice_and_collects_after_it_runs(direct_vm, bond, chain):
    post(direct_vm, bond)
    with direct_vm.prank(OWNER):
        assert bond.release(BOND_ID)["state"] == "leaving"

    set_date(direct_vm, PAST_NOTICE)
    got = bond.collect(BOND_ID)
    assert got["state"] == "released"
    assert chain.transfers == [{"to": OWNER, "value": DEPOSIT, "kind": EVM_PAY}]
    assert bond.reserves()["committed"] == "0"


def test_a_deposit_cannot_be_collected_before_the_notice_runs(direct_vm, bond, chain):
    post(direct_vm, bond)
    with direct_vm.prank(OWNER):
        bond.release(BOND_ID)

    set_date(direct_vm, "2026-10-10T23:00:00Z")
    with direct_vm.expect_revert("must run"):
        bond.collect(BOND_ID)
    assert chain.transfers == []


def test_a_bond_is_still_challengeable_during_the_notice(direct_vm, bond, chain):
    """The point of the notice: an owner cannot escape by being quick."""
    post(direct_vm, bond)
    with direct_vm.prank(OWNER):
        bond.release(BOND_ID)

    got = challenge(direct_vm, bond)
    assert got["state"] == "challenged"

    chain.set_claim(status="missing", checked_at=AFTER_CHALLENGE)
    set_date(direct_vm, AFTER_CHALLENGE)
    assert bond.resolve(BOND_ID)["outcome"] == "challenger"


def test_only_the_owner_may_give_notice(direct_vm, bond):
    post(direct_vm, bond)
    with direct_vm.prank(STRANGER):
        with direct_vm.expect_revert("only the owner"):
            bond.release(BOND_ID)


def test_a_challenged_bond_cannot_give_notice(direct_vm, bond):
    post(direct_vm, bond)
    challenge(direct_vm, bond)
    with direct_vm.prank(OWNER):
        with direct_vm.expect_revert("has to be resolved first"):
            bond.release(BOND_ID)


def test_collecting_without_notice_is_refused(direct_vm, bond):
    post(direct_vm, bond)
    with direct_vm.expect_revert("no notice has been given"):
        bond.collect(BOND_ID)


def test_a_released_bond_is_over_for_good(direct_vm, bond, chain):
    post(direct_vm, bond)
    with direct_vm.prank(OWNER):
        bond.release(BOND_ID)
    set_date(direct_vm, PAST_NOTICE)
    bond.collect(BOND_ID)

    with direct_vm.expect_revert("already over"):
        challenge(direct_vm, bond)
    assert len(chain.transfers) == 1


def test_the_terms_are_published_rather_than_implied(bond):
    got = bond.terms()
    assert got["challenge_fraction"] == "1/10"
    assert got["notice_seconds"] == str(NOTICE_SECONDS)


def test_the_directory_it_believes_is_fixed_not_chosen_by_a_caller(bond):
    """
    Otherwise anyone could deploy a directory that reports whatever they
    like, stake behind nothing, and never be slashed, while `backing`
    reported them as collateralised.
    """
    assert bond.settings()["link"].lower() == LINK_AT
