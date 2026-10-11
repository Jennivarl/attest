# ATTEST

Proving you control something off chain, and staking money on it.

Every directory on a blockchain has the same hole. Someone proves they control a domain, gets listed, and then quietly lets the proof lapse while the listing keeps vouching for them. There is no issuer to revoke it and nobody is paid to notice.

ATTEST closes that hole with four contracts: one that checks control, one that publishes it with an honest age on it, one that lets anyone put money on whether it still holds, and one that shows how to consume the result.

| contract | what it does |
|---|---|
| **PROOF** | A claimant publishes one line where only its controller could put it. Every validator fetches the document and checks. |
| **LINK** | The directory. Answers from the resource, not the address, and says how old each proof is. |
| **BOND** | Stake behind a binding. Anyone may stake against it, and whoever is right takes the other's money. |
| **GUARD** | The reference consumer: a gate that opens only for a live, fresh binding. |

No model is involved anywhere. Validators fetch and compare strings.

| | |
|---|---|
| Network | GenLayer Studio Next, chain id 61997, `https://studio-next.genlayer.com/api` |
| Engine | Consensus v0.6, GenVM v0.3, runner `py-genlayer:5jycge...` |
| PROOF | [`0x7eC8608A12ea2B0d763602f20EC42429172AD4C9`](https://explorer-studio-dev.genlayer.com/address/0x7eC8608A12ea2B0d763602f20EC42429172AD4C9) |
| LINK | [`0xc27Bf383784db5Bb8a02EA99708cc21fd492F0d6`](https://explorer-studio-dev.genlayer.com/address/0xc27Bf383784db5Bb8a02EA99708cc21fd492F0d6) |
| BOND | [`0x8Dbf08E28F72248E026Ac9331AE610dA651A1b7d`](https://explorer-studio-dev.genlayer.com/address/0x8Dbf08E28F72248E026Ac9331AE610dA651A1b7d) |
| GUARD | [`0xEB9432A809b2821db071eef02cCd6C4AD1ab621e`](https://explorer-studio-dev.genlayer.com/address/0xEB9432A809b2821db071eef02cCd6C4AD1ab621e) |
| Tests | 120, `python -m pytest -q` |

---

## Three outcomes, and the third is why this is safe

| outcome | what happened | may it cost anyone money |
|---|---|---|
| `verified` | the document was fetched and the token was on a line of its own | yes, the challenger loses their stake |
| `missing` | the document was fetched and the token was not in it | yes, the owner loses their deposit |
| `unreachable` | the document could not be fetched at all | **no, nothing moves** |

Collapsing `missing` into `unreachable` would not be untidy here, it would be the whole exploit. A deposit is slashed when a proof goes `missing`. If an outage counted as a withdrawal, taking anybody's money would just mean waiting for their host to have a bad minute, and the system would be a lottery on uptime rather than a check on honesty.

So when a check comes back `unreachable`, BOND decides nothing, moves nothing, and leaves the challenge open until someone can actually reach the host.

---

## How a proof is made hard to fake

**The claimant never chooses the evidence.** The token and the URL are both derived by the contract from the claiming address and the resource. A claimant who could name the path could point at a document they merely influence.

**The token is matched line by line, not as a substring.** A substring search would let an attacker pass by persuading a victim's site to echo arbitrary text, which search results and error pages do routinely. A page saying `Sorry, no results for genlayer-attest=0x...` proves nothing, and is tested.

**A handle claim has to clear two gates.** The gist must carry the token *and* the API's `owner.login` must match the claimed account. The token alone proves somebody wrote a file; the owner proves who. Copying a victim's token into your own gist gets you `missing`, naming the account it actually belongs to.

**Each address gets a different token for the same resource.** Otherwise one claimant's published file would prove everybody's claim.

**The token never changes.** It is derived from the address and the resource and not from the time, so a proof can be re-checked years later without the claimant touching anything.

---

## What consensus derived, and what was copied

| stored field | where it comes from | derived or copied |
|---|---|---|
| `status` | each validator fetched the document itself and classified its own response | derived |
| `note` | the reason, which validators must also agree on, not just the outcome | derived |
| `token`, `url` | built by the contract from the claimant's address and the resource | derived |
| `owner` | `gl.message.sender_address` | derived |
| `opened_at`, `checked_at` | the transaction's own `datetime`, part of the message | derived |

The leader's result is used for one thing: as the proposal a validator checks against its own. Two validators agreeing a proof failed while disagreeing about *why* is not agreement, so `note` is compared too.

---

## What a dishonest leader can try

| the lie | what it would buy | what happens | test |
|---|---|---|---|
| pass a proof that was never published | hands someone a domain they do not own | rejected | `test_a_validator_rejects_a_leader_who_passes_a_proof_that_is_not_published` |
| fail a proof that was published | strips someone of a proof they hold | rejected | `test_a_validator_rejects_a_leader_who_fails_a_proof_that_is_published` |
| call a live site unreachable | stalls a challenge indefinitely | rejected | `test_a_validator_rejects_a_leader_who_calls_a_live_site_unreachable` |
| call a down site a withdrawal | **slashes a deposit with nothing wrong** | rejected | `test_a_validator_rejects_a_leader_who_calls_a_down_site_a_withdrawal` |
| crash instead of answering | stalls the round | rejected: a malformed result is a disagreement, not an exception | `test_a_validator_rejects_a_leader_that_errored` |
| agree the outcome, differ on the reason | quietly changes what a claimant acts on | rejected | `test_validators_must_agree_on_the_reason_not_only_the_outcome` |

A validator that could not reach the host agrees only that it was unreachable, and will not attest to any outcome however plausible.

---

## Freshness, stated rather than implied

A proof is a statement about a moment. LINK reports the age of that moment instead of pretending it is now.

- A binding is **live** until its owner withdraws it or a `missing` check takes it down.
- A binding is **fresh** for 30 days after the proof was last checked. After that it stays live and stops counting.
- `controls()` is the narrow question a gate should ask, and it answers on *fresh*, so a lapsed proof stops opening doors with nobody having to revoke anything.
- An unreadable timestamp is never fresh. Doubt about a binding's age is a reason to distrust it, not to trust it.
- A `missing` check takes a binding down. An `unreachable` one does not, because an outage is not a withdrawal.

A resource can be held by more than one address at once, and LINK reports that as `contested` rather than picking a winner. Two people really can both control a domain, and choosing silently would be the directory inventing a fact.

---

## One way out at a time

A bond is somebody's money, so exactly one transition is ever available:

| state | what can happen |
|---|---|
| `bonded` | challenge it, or the owner gives notice |
| `challenged` | resolve it, on a check taken after the challenge |
| `leaving` | still challengeable, right to the last second |
| `settled` / `released` | nothing, ever again |

The notice period exists so an owner cannot escape a challenge by being quick. A challenge during the notice stops the exit and is decided on evidence.

Neither side can win on a reading that was already sitting there when they acted: `resolve` requires the check to be newer than the challenge.

---

## Verified live on Studio Next

| step | transaction | outcome |
|---|---|---|
| Deploy PROOF | [`0x473af790...`](https://explorer-studio-dev.genlayer.com/tx/0x473af79091c7906c9075b52a45663b287581b8564c6823de95a159dcc0b108a5) | source byte-identical to the bundle in this repo |
| Deploy LINK | [`0x1d5502b4...`](https://explorer-studio-dev.genlayer.com/tx/0x1d5502b48b38aa984c216d7a7a653cca1dbcbc646ce7f51eadbafda277159fcc) | |
| Deploy BOND | [`0x8f966b9b...`](https://explorer-studio-dev.genlayer.com/tx/0x8f966b9b84dc49d9a174ea225c1d482debebd726626b0244b28f8b471883f5dd) | |
| Deploy GUARD | [`0x1fbe9d17...`](https://explorer-studio-dev.genlayer.com/tx/0x1fbe9d176bc2395e2b320434c4facd99330a297027a8bab0dc3ebc51ee42ef24) | pointed at LINK |
| Claim `example.com` | [`0xcd8ae371...`](https://explorer-studio-dev.genlayer.com/tx/0xcd8ae3710ed0735b6e1dd0af85455c0f7db1a04529cb80f28d34079a811233ee) | token and URL derived by the contract |
| Check it | [`0x82bc78a6...`](https://explorer-studio-dev.genlayer.com/tx/0x82bc78a67e892ae1e0504e2e8027c477ca6915beca47628ef8b07aacf1dd0c62) | **`missing`**: `nothing is published at that address`. The site answered, and what it said was no |
| Claim a host that does not resolve | [`0x09949a98...`](https://explorer-studio-dev.genlayer.com/tx/0x09949a98b55953d3fc9ab0f7c1fd61c46b9daa4817059a4a93498b946c49ec0c) | |
| Check it | [`0x46236b18...`](https://explorer-studio-dev.genlayer.com/tx/0x46236b1841f6ab1eb34efcb29a23b5731d97ca4b698236d5d83c1dab4ad350c5) | **`unreachable`**: `the document could not be fetched`. Same contract, different claim about the world |

Those two rows are the design in one place: the same contract, reading two real hosts, and refusing to call them the same thing.

---

## Limits, stated plainly

- **A proof is about a moment, not forever.** Nothing expires on its own; LINK reports age and a consumer decides. That is deliberate, because silent expiry is how directories lie.
- **Control is not ownership.** Anyone who can publish at a domain can prove it, which includes a hosting provider or anyone with write access to the site. That is the same assumption every ACME certificate makes, and it is worth knowing.
- **A handle proof trusts GitHub's API** to report the gist's owner honestly. There is no way around that: the account only exists because GitHub says so.
- **HTTP only.** A claim is as strong as the TLS between the validators and the host.
- **`contested` is reported, not resolved.** Two addresses can hold one resource. A consumer that cannot tolerate that should refuse.
- **A challenge needs somebody to trigger a fresh check.** BOND cannot make PROOF fetch anything inside one transaction, so the sequence is challenge, then check, then resolve. The check is permissionless, so anyone with an interest can do it.
- **A deposit must be at least ten units** or the challenger's tenth rounds to nothing and the bond could never be challenged.

---

## Running it

```sh
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
python -m deploy.build_bundle          # the four files that actually deploy
python -m pytest -q                    # 120 tests, no chain and no network
```

The tests run the contracts in genlayer-test's direct mode, in process. There is no node to start and nothing is fetched: the claimed domain and GitHub's API are answered from the test, so a test can produce states that are tedious to arrange for real, including a site that is down, a gist owned by somebody else, and a page that echoes the token without publishing it.

Direct mode loads one contract per process, so LINK, BOND and GUARD are tested against stubs that speak the real cross-contract protocol: the contract genuinely calls out, and the reply is genuinely calldata encoded.

To drive the live copies:

```sh
npm install
export GENLAYER_KEYSTORE=~/.genlayer/keystores/mykey.json
export GENLAYER_PASSWORD=...
node deploy/demo.mjs token domain example.com     # what to publish, and where
node deploy/demo.mjs domain example.com           # open the claim
node deploy/demo.mjs check <claim_id>             # fetch it, under consensus
```

Addresses live in [`deploy/live.json`](deploy/live.json). The password is read from the environment and is never stored in this repo.

---

## Layout

```
contracts/resource.py       the pure part: validation, token derivation, matching, dates
contracts/proof.py          control checks, under consensus
contracts/link.py           the directory, with freshness
contracts/bond.py           money staked on a proof still holding
contracts/guard.py          reference consumer: a gate on a live binding
contracts/*_bundle.py       what actually deploys. Generated, never edited
deploy/build_bundle.py      builds them; a test fails if a committed one is stale
deploy/chain.mjs            network, signing and payout fee allocations
deploy/deploy.mjs           deploy one bundle
deploy/verify.mjs           prove a live contract runs this exact bundle
deploy/demo.mjs             drive the live copies
test/                       120 tests
```

GenVM loads a single source file with no access to siblings, so each bundle pastes a contract together with the pure module it imports. Comments and docstrings are stripped from the deployed copy, because a deploy costs gas per byte and a bundle over the cap returns a transaction hash and then simply does not exist. Edit the modules, never a bundle: a test regenerates them and fails if a committed copy has drifted.

## License

MIT
