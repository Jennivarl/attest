// The live contracts, one step per run, so every result is checked before the next.
//
//   npm install
//   export GENLAYER_KEYSTORE=~/.genlayer/keystores/mykey.json
//   export GENLAYER_PASSWORD=...           # never stored in this repo
//
//   node deploy/demo.mjs show
//   node deploy/demo.mjs token  <kind> <resource>        what to publish
//   node deploy/demo.mjs domain <domain>                 open a domain claim
//   node deploy/demo.mjs handle <handle> <gist_id>       open a handle claim
//   node deploy/demo.mjs check  <claim_id>               fetch it, under consensus
//   node deploy/demo.mjs adopt  <claim_id>               publish it in the directory
//   node deploy/demo.mjs post   <binding_id> <GEN>       stake behind it
//   node deploy/demo.mjs challenge <bond_id> <GEN>       stake against it
//   node deploy/demo.mjs resolve   <bond_id>             settle it
//
// Addresses come from live.json, written by deploy/deploy.mjs.
import { readFileSync } from "node:fs";
import {
  MESSAGE_ALLOCATION_ROOT_PARENT_INDEX,
  MessageType,
  encodeExternalMessageFeeParams,
} from "genlayer-js";
import { balance, connect, estimateFees, fmt, gen, txUrl, waitFor } from "./chain.mjs";

const live = JSON.parse(readFileSync(new URL("./live.json", import.meta.url), "utf8"));
const { proof: PROOF, link: LINK, bond: BOND } = live;
if (!PROOF || !LINK || !BOND) throw new Error("deploy/live.json has no addresses yet");

const { account, client } = await connect();
const view = (address, fn, args = []) => client.readContract({ address, functionName: fn, args });

const PAYOUT_GAS_LIMIT = 200_000n;
const PAYOUT_MAX_GAS_PRICE = 400_000_000n;

/**
 * The fee a payout needs reserved before value can leave a contract.
 *
 * Paying a wallet is an external message, and a transaction that does not
 * reserve a budget for one dies with `out_of message_fee total # external`.
 * Neither estimator produces the allocation on its own, so it is declared
 * here and priced by the generic estimate, which is the only one that fills
 * `totalMessageFees`.
 */
function payoutAllocation(recipient) {
  return {
    messageType: MessageType.External,
    onAcceptance: false,
    parentIndex: MESSAGE_ALLOCATION_ROOT_PARENT_INDEX,
    recipient,
    budget: PAYOUT_GAS_LIMIT * PAYOUT_MAX_GAS_PRICE,
    feeParams: encodeExternalMessageFeeParams({
      gasLimit: PAYOUT_GAS_LIMIT,
      maxGasPrice: PAYOUT_MAX_GAS_PRICE,
    }),
  };
}

async function write(address, functionName, args, value = 0n, payTo = undefined) {
  const allocations = payTo ? [payoutAllocation(payTo)] : undefined;
  const fees = await estimateFees(client, {
    address,
    functionName,
    args,
    value,
    ...(allocations ? { messageAllocations: allocations } : {}),
  });
  const txId = await client.writeContract({
    address,
    functionName,
    args,
    value,
    // Each check is one web fetch per validator, and a rotation costs a round.
    consensusMaxRotations: 8,
    ...(fees ? { fees: { ...fees, ...(allocations ? { messageAllocations: allocations } : {}) } } : {}),
  });
  const receipt = await waitFor(client, txId);
  console.log("  ", txUrl(txId));
  if (!receipt.ok) console.log("   returned nothing:", receipt.outcome);
  return receipt;
}

async function show() {
  const claims = await view(PROOF, "claim_list");
  for (const id of claims) {
    const c = await view(PROOF, "get_claim", [id]);
    console.log(`claim ${id}\n   [${c.status || "never checked"}] ${c.url}\n   ${c.note}`);
  }
  const bindings = await view(LINK, "binding_list");
  for (const id of bindings) {
    const b = await view(LINK, "get_binding", [id]);
    console.log(`binding ${id}\n   live=${b.live} fresh=${b.fresh} age=${b.age_seconds}s`);
  }
  const bonds = await view(BOND, "bond_list");
  for (const id of bonds) {
    const b = await view(BOND, "get_bond", [id]);
    console.log(`bond ${id}\n   [${b.state}] ${b.amount} | ${b.reason || "-"}`);
  }
  console.log("committed", (await view(BOND, "reserves")).committed);
}

const [step, ...rest] = process.argv.slice(2);
console.log("wallet", account.address, fmt(await balance(account.address)), "\n");

if (step === "token") {
  const [kind, resource] = rest;
  console.log("publish this line:");
  console.log(" ", await view(PROOF, "token_for_claim", [account.address, kind, resource]));
  if (kind === "domain") console.log("at:", `https://${resource}/.well-known/genlayer-attest.txt`);
} else if (step === "domain") {
  const got = await write(PROOF, "claim_domain", [rest[0]]);
  console.log("result", JSON.stringify(got?.result?.payload ?? ""));
} else if (step === "handle") {
  await write(PROOF, "claim_handle", [rest[0], rest[1]]);
} else if (step === "check") {
  await write(PROOF, "check", [rest[0]]);
  const c = await view(PROOF, "get_claim", [rest[0]]);
  console.log(`\n=> ${c.status.toUpperCase()}: ${c.note}`);
  console.log(`   ${c.url}`);
} else if (step === "adopt") {
  await write(LINK, "adopt", [rest[0]]);
} else if (step === "refresh") {
  await write(LINK, "refresh", [rest[0]]);
} else if (step === "post") {
  await write(BOND, "post", [rest[0]], gen(rest[1]));
} else if (step === "challenge") {
  await write(BOND, "challenge", [rest[0]], gen(rest[1]));
} else if (step === "resolve") {
  const before = await view(BOND, "get_bond", [rest[0]]);
  const owner = before.owner;
  const challenger = before.challenger;
  console.log("owner      ", owner, fmt(await balance(owner)));
  console.log("challenger ", challenger, fmt(await balance(challenger)));
  // Either side may be paid, so the allocation names whoever the contract
  // will actually pay rather than guessing.
  await write(BOND, "resolve", [rest[0]], 0n, owner);
  const after = await view(BOND, "get_bond", [rest[0]]);
  console.log(`\n=> ${after.state} / ${after.outcome || "undecided"}: ${after.reason}`);
  console.log("owner      ", fmt(await balance(owner)));
  console.log("challenger ", fmt(await balance(challenger)));
} else if (step !== "show") {
  console.log("steps: show, token, domain, handle, check, adopt, refresh, post, challenge, resolve");
  process.exit(1);
}

console.log("");
await show();
