"""
PROOF run for real, in-process, by genlayer-test's direct mode.

No chain and no network: the claimed domain and GitHub's API are answered
from here, so a test can put either into states that are tedious to produce
for real, including a site that is down, a gist owned by somebody else, and
a page that echoes the token without publishing it.

Half of these run the validator side, because a leader that reaches the
right answer proves nothing. What matters is whether an honest validator
would agree, and whether a dishonest one would be caught. Each lie here is
one a leader would have an actual reason to tell: pass a proof that was
never published, or fail one that was.
"""

import json
import sys
from pathlib import Path

import pytest

BUNDLE = str(Path(__file__).resolve().parent.parent / "contracts" / "proof_bundle.py")

OWNER = b"\xa0" * 20
STRANGER = b"\xb0" * 20

DOMAIN = "example.com"
HANDLE = "jennivarl"
GIST = "a1b2c3d4e5f6"

VERIFIED, MISSING, UNREACHABLE = "verified", "missing", "unreachable"

OPENED = "2026-10-10T09:00:00Z"


def token(owner: bytes, kind: str, resource: str) -> str:
    return "genlayer-attest=0x" + owner.hex() + ":" + kind + ":" + resource


def set_date(vm, stamp: str) -> None:
    vm.warp(stamp)
    message = sys.modules.get("genlayer.message")
    if message is not None and isinstance(getattr(message, "raw", None), dict):
        message.raw["datetime"] = stamp
    gl = sys.modules.get("genlayer.gl")
    if gl is not None and getattr(gl, "message_raw", None) is not None:
        gl.message_raw["datetime"] = stamp


def serve(vm, pattern: str, body, status: int = 200) -> None:
    vm.clear_mocks()
    text = body if isinstance(body, str) else json.dumps(body)
    vm.mock_web(pattern, {"status": status, "body": text})


def serve_domain(vm, body, status: int = 200, domain: str = DOMAIN) -> None:
    serve(vm, domain.replace(".", r"\.") + r"/\.well-known/genlayer-attest\.txt", body, status)


def gist_doc(login: str = HANDLE, content: str = "", truncated: bool = False) -> dict:
    """The shape GitHub's gist API actually returns, trimmed to what is read."""
    return {
        "id": GIST,
        "owner": {"login": login, "id": 12345},
        "files": {"proof.txt": {"filename": "proof.txt", "content": content, "truncated": truncated}},
    }


def serve_gist(vm, document, status: int = 200) -> None:
    serve(vm, r"api\.github\.com/gists/" + GIST, document, status)


@pytest.fixture
def proof(direct_vm, direct_deploy):
    contract = direct_deploy(BUNDLE)
    direct_vm.strict_mocks = False
    set_date(direct_vm, OPENED)
    return contract


@pytest.fixture
def domain_claim(direct_vm, proof):
    with direct_vm.prank(OWNER):
        got = proof.claim_domain(DOMAIN)
    return got["claim_id"]


@pytest.fixture
def handle_claim(direct_vm, proof):
    with direct_vm.prank(OWNER):
        got = proof.claim_handle(HANDLE, GIST)
    return got["claim_id"]


def leader(status: str, note: str) -> dict:
    """The shape PROOF's leader returns, so a test can fake a dishonest one."""
    return {"status": status, "note": note}


# --------------------------------------------------------------------
# opening a claim
# --------------------------------------------------------------------


def test_a_claim_says_exactly_what_to_publish_and_where(direct_vm, proof):
    with direct_vm.prank(OWNER):
        got = proof.claim_domain(DOMAIN)

    assert got["token"] == token(OWNER, "domain", DOMAIN)
    assert got["url"] == "https://example.com/.well-known/genlayer-attest.txt"
    assert got["status"] == ""
    assert got["claim_id"] == "0x" + OWNER.hex() + ":domain:" + DOMAIN


def test_the_token_can_be_read_before_opening_anything(proof):
    """So a claimant can publish the file first and never waste a transaction."""
    assert proof.token_for_claim("0x" + OWNER.hex(), "domain", DOMAIN) == token(OWNER, "domain", DOMAIN)


def test_two_addresses_get_different_tokens_for_the_same_domain(direct_vm, proof):
    """Otherwise one claimant's published file would prove everybody's claim."""
    with direct_vm.prank(OWNER):
        mine = proof.claim_domain(DOMAIN)
    with direct_vm.prank(STRANGER):
        theirs = proof.claim_domain(DOMAIN)
    assert mine["token"] != theirs["token"]
    assert mine["claim_id"] != theirs["claim_id"]


def test_a_claim_cannot_be_reopened_because_somebody_may_be_relying_on_it(direct_vm, proof):
    with direct_vm.prank(OWNER):
        proof.claim_domain(DOMAIN)
        with direct_vm.expect_revert("already exists"):
            proof.claim_domain(DOMAIN)


@pytest.mark.parametrize(
    "domain",
    ["https://example.com", "example.com/path", "localhost", "example.com:8443", "", "-bad.com", "a..b.com"],
)
def test_anything_that_is_not_a_bare_hostname_is_refused(direct_vm, proof, domain):
    with direct_vm.prank(OWNER):
        with direct_vm.expect_revert("bare hostname"):
            proof.claim_domain(domain)


@pytest.mark.parametrize("handle", ["", "-nope", "no--pe", "x" * 40, "has space", "has/slash"])
def test_a_handle_that_github_could_not_issue_is_refused(direct_vm, proof, handle):
    with direct_vm.prank(OWNER):
        with direct_vm.expect_revert("username shape"):
            proof.claim_handle(handle, GIST)


@pytest.mark.parametrize("gist", ["", "../secrets", "zzz", "g" * 70, "abc def"])
def test_a_gist_id_that_could_leave_the_path_is_refused(direct_vm, proof, gist):
    with direct_vm.prank(OWNER):
        with direct_vm.expect_revert("hexadecimal"):
            proof.claim_handle(HANDLE, gist)


# --------------------------------------------------------------------
# checking a domain
# --------------------------------------------------------------------


def test_a_published_token_verifies(direct_vm, proof, domain_claim):
    serve_domain(direct_vm, "hello\n" + token(OWNER, "domain", DOMAIN) + "\nbye")
    got = proof.check(domain_claim)

    assert got["status"] == VERIFIED
    assert got["checked_at"] == OPENED
    assert proof.is_verified(domain_claim) is True
    assert direct_vm.run_validator() is True


def test_a_document_without_the_token_is_missing_not_unreachable(direct_vm, proof, domain_claim):
    serve_domain(direct_vm, "nothing to see here")
    got = proof.check(domain_claim)

    assert got["status"] == MISSING
    assert proof.is_verified(domain_claim) is False
    assert direct_vm.run_validator() is True


def test_nothing_published_at_the_path_is_missing(direct_vm, proof, domain_claim):
    """404 is the site answering: the file is not there."""
    serve_domain(direct_vm, "Not Found", status=404)
    got = proof.check(domain_claim)

    assert got["status"] == MISSING
    assert "nothing is published" in got["note"]
    assert direct_vm.run_validator() is True


@pytest.mark.parametrize("status", [500, 502, 503, 429])
def test_a_host_that_is_down_is_unreachable_not_missing(direct_vm, proof, domain_claim, status):
    """
    The distinction the whole design rests on. Downstream a bond is slashed
    when a proof goes missing, so an outage must never look like a withdrawal.
    """
    serve_domain(direct_vm, "oh dear", status=status)
    got = proof.check(domain_claim)

    assert got["status"] == UNREACHABLE
    assert direct_vm.run_validator() is True


def test_a_token_that_only_appears_inside_another_line_does_not_verify(direct_vm, proof, domain_claim):
    """
    A substring search would let an attacker pass by persuading a victim's
    site to echo arbitrary text, which search and error pages do routinely.
    """
    serve_domain(direct_vm, "Sorry, no results for " + token(OWNER, "domain", DOMAIN) + " on this site")
    assert proof.check(domain_claim)["status"] == MISSING


def test_another_addresss_token_does_not_prove_this_claim(direct_vm, proof, domain_claim):
    serve_domain(direct_vm, token(STRANGER, "domain", DOMAIN))
    assert proof.check(domain_claim)["status"] == MISSING


def test_a_claim_can_be_checked_again_and_the_record_moves(direct_vm, proof, domain_claim):
    serve_domain(direct_vm, token(OWNER, "domain", DOMAIN))
    assert proof.check(domain_claim)["status"] == VERIFIED

    set_date(direct_vm, "2026-11-01T09:00:00Z")
    serve_domain(direct_vm, "we took it down")
    got = proof.check(domain_claim)

    assert got["status"] == MISSING
    assert got["checked_at"] == "2026-11-01T09:00:00Z"


def test_checking_an_unknown_claim_is_refused(direct_vm, proof):
    with direct_vm.expect_revert("no such claim"):
        proof.check("0xdead:domain:example.com")


# --------------------------------------------------------------------
# checking a handle, where the owner is the whole point
# --------------------------------------------------------------------


def test_a_gist_by_the_right_account_carrying_the_token_verifies(direct_vm, proof, handle_claim):
    serve_gist(direct_vm, gist_doc(content="notes\n" + token(OWNER, "handle", HANDLE)))
    got = proof.check(handle_claim)

    assert got["status"] == VERIFIED
    assert direct_vm.run_validator() is True


def test_the_right_token_in_somebody_elses_gist_proves_nothing(direct_vm, proof, handle_claim):
    """
    The attack this exists to stop: copy a victim's token into your own gist
    and claim their handle. The owner field decides, not the content.
    """
    serve_gist(direct_vm, gist_doc(login="impostor", content=token(OWNER, "handle", HANDLE)))
    got = proof.check(handle_claim)

    assert got["status"] == MISSING
    assert "belongs to impostor" in got["note"]
    assert direct_vm.run_validator() is True


def test_the_owner_is_matched_without_regard_to_case(direct_vm, proof, handle_claim):
    """GitHub logins are case insensitive and a proof must not turn on case."""
    serve_gist(direct_vm, gist_doc(login="JenniVarl", content=token(OWNER, "handle", HANDLE)))
    assert proof.check(handle_claim)["status"] == VERIFIED


def test_the_right_account_without_the_token_is_missing(direct_vm, proof, handle_claim):
    serve_gist(direct_vm, gist_doc(content="just some notes"))
    assert proof.check(handle_claim)["status"] == MISSING


def test_a_truncated_gist_file_is_not_read(direct_vm, proof, handle_claim):
    """Half a document is not evidence, so a truncated file counts as absent."""
    serve_gist(direct_vm, gist_doc(content=token(OWNER, "handle", HANDLE), truncated=True))
    assert proof.check(handle_claim)["status"] == MISSING


def test_an_api_that_does_not_answer_json_is_unreachable(direct_vm, proof, handle_claim):
    serve_gist(direct_vm, "<html>rate limited</html>")
    assert proof.check(handle_claim)["status"] == UNREACHABLE


def test_a_gist_that_does_not_exist_is_missing(direct_vm, proof, handle_claim):
    serve_gist(direct_vm, {"message": "Not Found"}, status=404)
    assert proof.check(handle_claim)["status"] == MISSING


# --------------------------------------------------------------------
# what a validator will and will not agree to
# --------------------------------------------------------------------


def test_a_validator_rejects_a_leader_who_passes_a_proof_that_is_not_published(direct_vm, proof, domain_claim):
    """The lie that matters: it would hand somebody a domain they do not own."""
    serve_domain(direct_vm, "nothing here")
    proof.check(domain_claim)
    assert direct_vm.run_validator(leader_result=leader(VERIFIED, "the token is published at that domain")) is False


def test_a_validator_rejects_a_leader_who_fails_a_proof_that_is_published(direct_vm, proof, domain_claim):
    """The other direction: it would strip somebody of a proof they hold."""
    serve_domain(direct_vm, token(OWNER, "domain", DOMAIN))
    proof.check(domain_claim)
    assert direct_vm.run_validator(leader_result=leader(MISSING, "the document does not carry the token")) is False


def test_a_validator_rejects_a_leader_who_calls_a_live_site_unreachable(direct_vm, proof, domain_claim):
    serve_domain(direct_vm, token(OWNER, "domain", DOMAIN))
    proof.check(domain_claim)
    assert direct_vm.run_validator(leader_result=leader(UNREACHABLE, "the document could not be fetched")) is False


def test_a_validator_rejects_a_leader_who_calls_a_down_site_a_withdrawal(direct_vm, proof, domain_claim):
    """
    An outage reported as `missing` is how an attacker would slash a bond
    downstream without the claimant ever doing anything wrong.
    """
    serve_domain(direct_vm, "oh dear", status=503)
    proof.check(domain_claim)
    assert direct_vm.run_validator(leader_result=leader(MISSING, "the document does not carry the token")) is False


def test_a_validator_that_cannot_reach_it_agrees_only_that_it_was_unreachable(direct_vm, proof, domain_claim):
    serve_domain(direct_vm, "oh dear", status=503)
    proof.check(domain_claim)

    assert direct_vm.run_validator(leader_result=leader(UNREACHABLE, "the host answered 503")) is True
    assert direct_vm.run_validator(leader_result=leader(VERIFIED, "the token is published at that domain")) is False
    assert direct_vm.run_validator(leader_result=leader(MISSING, "nothing is published at that address")) is False


def test_a_validator_rejects_a_leader_that_errored(direct_vm, proof, domain_claim):
    serve_domain(direct_vm, token(OWNER, "domain", DOMAIN))
    proof.check(domain_claim)
    assert direct_vm.run_validator(leader_error=Exception("boom")) is False


def test_a_validator_rejects_a_result_of_the_wrong_shape(direct_vm, proof, domain_claim):
    serve_domain(direct_vm, token(OWNER, "domain", DOMAIN))
    proof.check(domain_claim)
    assert direct_vm.run_validator(leader_result={"nonsense": True}) is False


def test_validators_must_agree_on_the_reason_not_only_the_outcome(direct_vm, proof, handle_claim):
    """
    Two validators agreeing a proof failed while disagreeing about why is not
    agreement, and downstream the reason is what a claimant acts on.
    """
    serve_gist(direct_vm, gist_doc(login="impostor", content=token(OWNER, "handle", HANDLE)))
    proof.check(handle_claim)

    assert direct_vm.run_validator(leader_result=leader(MISSING, "that gist belongs to impostor")) is True
    assert direct_vm.run_validator(leader_result=leader(MISSING, "the gist does not carry the token")) is False
