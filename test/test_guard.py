"""
GUARD, the reference consumer, deployed and run.

An example nobody runs is a comment that rots. These deploy the file exactly
as it ships and check the one property it exists to demonstrate: the gate
asks the directory live, so a proof that lapses closes the door by itself.
"""

import sys
from pathlib import Path

import pytest

from test.stub_proof import DOMAIN, OWNER, STRANGER

BUNDLE = str(Path(__file__).resolve().parent.parent / "contracts" / "guard_bundle.py")
LINK_AT = "0x" + ("cc" * 20)
NOW = "2026-10-10T09:00:00Z"


def set_date(vm, stamp: str) -> None:
    vm.warp(stamp)
    message = sys.modules.get("genlayer.message")
    if message is not None and isinstance(getattr(message, "raw", None), dict):
        message.raw["datetime"] = stamp
    gl = sys.modules.get("genlayer.gl")
    if gl is not None and getattr(gl, "message_raw", None) is not None:
        gl.message_raw["datetime"] = stamp


class LinkStub:
    """Answers `controls`, which is the only thing a gate asks."""

    def __init__(self):
        self.answer = True
        self.asked = []

    def install(self, vm):
        def hook(_vm, request):
            from gltest.direct.sdk_compat import import_calldata

            calldata = import_calldata()
            if "CallContract" not in request:
                return None
            call = request["CallContract"]
            self.asked.append(call["calldata"][""])
            return bytes([0]) + calldata.encode(self.answer)

        vm._gl_call_hook = hook
        return self


@pytest.fixture
def link(direct_vm):
    return LinkStub().install(direct_vm)


@pytest.fixture
def guard(direct_vm, direct_deploy, link):
    set_date(direct_vm, NOW)
    with direct_vm.prank(OWNER):
        contract = direct_deploy(BUNDLE, link=LINK_AT)
    direct_vm.strict_mocks = False
    with direct_vm.prank(OWNER):
        contract.allow("domain", DOMAIN)
    return contract


def test_a_controller_of_an_allowed_resource_may_act(direct_vm, guard, link):
    with direct_vm.prank(OWNER):
        got = guard.act("shipped a release")

    assert got["acted"] is True
    assert got["as"] == "domain:" + DOMAIN
    assert len(guard.actions()) == 1


def test_an_address_controlling_nothing_is_refused(direct_vm, guard, link):
    link.answer = False
    with direct_vm.prank(STRANGER):
        with direct_vm.expect_revert("does not control"):
            guard.act("let me in")
    assert guard.actions() == []


def test_the_gate_asks_the_directory_live_rather_than_caching(direct_vm, guard, link):
    """A cached answer is exactly how a lapsed proof keeps opening a door."""
    with direct_vm.prank(OWNER):
        guard.act("first")
    link.answer = False
    with direct_vm.prank(OWNER):
        with direct_vm.expect_revert("does not control"):
            guard.act("second")
    assert link.asked == ["controls", "controls"]


def test_the_action_records_which_resource_let_the_caller_in(direct_vm, guard):
    with direct_vm.prank(OWNER):
        guard.act("audited later")
    record = guard.actions()[0]
    assert record["resource"] == DOMAIN
    assert record["kind"] == "domain"
    assert record["at"] == NOW


def test_only_the_operator_may_change_the_list(direct_vm, guard):
    with direct_vm.prank(STRANGER):
        with direct_vm.expect_revert("only the operator"):
            guard.allow("domain", "sneaky.example")


def test_a_resource_cannot_be_allowed_twice(direct_vm, guard):
    with direct_vm.prank(OWNER):
        with direct_vm.expect_revert("already allowed"):
            guard.allow("domain", DOMAIN)


def test_the_settings_name_the_directory_it_trusts(guard):
    assert guard.settings()["link"].lower() == LINK_AT
