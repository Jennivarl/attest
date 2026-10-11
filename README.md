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
| PROOF | [`0xfd7cF85675B439C74E494018E4a8C9B8cce94774`](https://explorer-studio-dev.genlayer.com/address/0xfd7cF85675B439C74E494018E4a8C9B8cce94774) |
| LINK | [`0x623250296Ab340a620FFF2fBFA3cE0F75BA2f278`](https://explorer-studio-dev.genlayer.com/address/0x623250296Ab340a620FFF2fBFA3cE0F75BA2f278) |
| BOND | [`0xe9166aeeBd401384B4BF0D8E5357A9b219365E59`](https://explorer-studio-dev.genlayer.com/address/0xe9166aeeBd401384B4BF0D8E5357A9b219365E59) |
| GUARD | [`0xD659085A558093b13B3EA37fb75C1aD995573338`](https://explorer-studio-dev.genlayer.com/address/0xD659085A558093b13B3EA37fb75C1aD995573338) |
| Tests | 122, `python -m pytest -q` |

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

## Nothing chooses who it believes at call time

Each contract here reads another, and every one of those addresses is fixed when the contract is deployed. None of them is ever an argument.

| contract | reads | fixed how |
|---|---|---|
| LINK | PROOF | constructor |
| BOND | LINK, and the PROOF named by the binding it read | constructor, then transitively |
| GUARD | LINK | constructor |

This is the single most important thing in the design and it is worth being blunt about why. An earlier version of `LINK.adopt` took the prover's address as an argument. Anyone could deploy a contract whose `get_claim` returned `verified` for anything, point the directory at it, and be listed as controlling a domain they had never touched. The same mistake in `BOND.post` would have let somebody stake behind nothing and never be slashed, while `backing()` reported them as collateralised.

A contract that lets its caller choose who it believes has no guarantees at all, only the appearance of them. `settings()` on LINK and BOND publishes what each one trusts, so a consumer can check rather than assume.

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

Every row is a real transaction, against real hosts, with nothing mocked.

### The three outcomes

| step | transaction | outcome |
|---|---|---|
| Claim the GitHub handle `jennivarl` | [`0x397b3fbb...`](https://explorer-studio-dev.genlayer.com/tx/0x397b3fbbef63f91dec53af293aa7108315d9fde029a47e02a53681dcaf6efb7f) | against a real public gist |
| Check it | [`0xe4d245d0...`](https://explorer-studio-dev.genlayer.com/tx/0xe4d245d031d8773b9b169fbe46114fc8411dd4546443fc570f02dd58160c6572) | **`verified`**: `the token is published by that account`. Validators fetched GitHub's API, matched `owner.login` and found the token on a line of its own |
| Claim `example.com` | [`0x0076dc46...`](https://explorer-studio-dev.genlayer.com/tx/0x0076dc46453a2a776e4d96e3ba9bebf832e1af0a39abb74f3e37caa02437cde8) | token and URL derived by the contract |
| Check it | [`0xf75ffa19...`](https://explorer-studio-dev.genlayer.com/tx/0xf75ffa19f6064d847ed9cb942b88d49a951f4b0ecb27ca6729653ff657df9fc5) | **`missing`**: `nothing is published at that address`. The site answered, and what it said was no |
| Claim a host that does not resolve | [`0xf9e2d175...`](https://explorer-studio-dev.genlayer.com/tx/0xf9e2d1759764dd5640a3e5c69c450cca18ea3fcc37c90aeb04c8ed93d4261665) | |
| Check it | [`0x88278b9a...`](https://explorer-studio-dev.genlayer.com/tx/0x88278b9afb674a5e34b60e9c8e5d40efd4dd41ad5787fcc1b0e1ba463782911f) | **`unreachable`**: `the document could not be fetched`. Same contract, a different claim about the world |

### The money

| step | transaction | outcome |
|---|---|---|
| Publish the verified claim in the directory | [`0xb2197ffd...`](https://explorer-studio-dev.genlayer.com/tx/0xb2197ffd8fc8a555fa2135790842fb26fb3ad9f0156c1ec45875f0efe8c56ff6) | LINK refuses anything not verified, reads it from the PROOF it was deployed against, and only the prover may publish it |
| Stake 0.2 GEN behind it | [`0x833f6793...`](https://explorer-studio-dev.genlayer.com/tx/0x833f679319b1ff5ddc923c9223797a65267e0fcac5ba84859107c8d0947b9a87) | the binding had to be live and fresh |
| A second account stakes 0.02 against it | [`0x458cfacf...`](https://explorer-studio-dev.genlayer.com/tx/0x458cfacf17a740c7dd7d50f48d7514f68ff196a78d470950a711ac1cb3c85ab4) | a tenth of the deposit, from a different address |
| Resolve immediately | [`0xbb8367ad...`](https://explorer-studio-dev.genlayer.com/tx/0xbb8367ad2c4b85a180ba82ac339882abbbbabe51dc99e27db13c54a20b659302) | **decided nothing**: `no check has been taken since the challenge`. Neither side may win on a reading that was already sitting there |
| Take a fresh check | [`0x5043c35a...`](https://explorer-studio-dev.genlayer.com/tx/0x5043c35a6d7ee91bc1f66986b01cd67e1fc91bcdd5698a283cae6442d95ed493) | still `verified` |
| Resolve | [`0x851f6400...`](https://explorer-studio-dev.genlayer.com/tx/0x851f64000a2fb35237c4387b5129218e307ca65dadb2039eb5f521d647d85f50) | **the owner took both**: 37.390457 to 37.610331 GEN, the 0.2 deposit plus the challenger's 0.02 stake. `committed` fell to 0 |

The deploys, in dependency order: PROOF [`0xdbfe8a44...`](https://explorer-studio-dev.genlayer.com/tx/0xdbfe8a447093cd4ab2e1ac292a154f7f285c85a01fb66432d2efa0a54c0bf596), LINK [`0x27323560...`](https://explorer-studio-dev.genlayer.com/tx/0x27323560419e7ae4d9967c73be290b15e997870a7725c980c6c485c158d77743), BOND [`0xd10f7d4b...`](https://explorer-studio-dev.genlayer.com/tx/0xd10f7d4bf430f4d716696fea41032cf9d7ee7eb87f2c9512b2a088f0e9f9b650), GUARD [`0x1a63e6af...`](https://explorer-studio-dev.genlayer.com/tx/0x1a63e6afe86ef645981f7422e877b8230ef41aecc3ee6fe4411de7049fd999d0). Each was told who it trusts as it went up.

**Not shown live:** a challenger winning, which needs a published proof to actually be taken down. The `missing` outcome that would trigger it is proven live above, and the slash itself is covered by the test suite.

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
python -m pytest -q                    # 122 tests, no chain and no network
```

The tests run the contracts in genlayer-test's direct mode, in process. There is no node to start and nothing is fetched: the claimed domain and GitHub's API are answered from the test, so a test can produce states that are tedious to arrange for real, including a site that is down, a gist owned by somebody else, and a page that echoes the token without publishing it.

Direct mode loads one contract per process, so LINK, BOND and GUARD are tested against stubs that speak the real cross-contract protocol: the contract genuinely calls out, and the reply is genuinely calldata encoded.

To drive the live copies:

Deployment order matters, because each contract is told who it trusts:

```sh
node deploy/deploy.mjs contracts/proof_bundle.py
node deploy/deploy.mjs contracts/link_bundle.py  <proof address>
node deploy/deploy.mjs contracts/bond_bundle.py  <link address>
node deploy/deploy.mjs contracts/guard_bundle.py <link address>
```

```sh
npm install
export GENLAYER_KEYSTORE=~/.genlayer/keystores/mykey.json
export GENLAYER_PASSWORD=...
node deploy/demo.mjs token domain example.com     # what to publish, and where
node deploy/demo.mjs domain example.com           # open the claim
node deploy/demo.mjs check <claim_id>             # fetch it, under consensus
node deploy/demo.mjs adopt <claim_id>             # publish it in the directory
node deploy/demo.mjs post  <binding_id> 0.2       # stake behind it
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
test/                       122 tests
```

GenVM loads a single source file with no access to siblings, so each bundle pastes a contract together with the pure module it imports. Comments and docstrings are stripped from the deployed copy, because a deploy costs gas per byte and a bundle over the cap returns a transaction hash and then simply does not exist. Edit the modules, never a bundle: a test regenerates them and fails if a committed copy has drifted.

## License

MIT
