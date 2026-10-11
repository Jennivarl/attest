"""
A stand-in PROOF, for testing the contracts that read one.

Direct mode loads one contract per process, so LINK and BOND cannot be
tested against the real PROOF in the same test. This answers the
cross-contract call with the protocol the chain actually uses, which means
the contract really does call out and the reply really is calldata encoded.

What a stub buys is states that are tedious to produce for real: a claim
that is verified, one that has gone missing, one whose host is merely down,
and one belonging to somebody else.
"""

OWNER = b"\xa0" * 20
STRANGER = b"\xb0" * 20

DOMAIN = "example.com"
CLAIM = "0x" + OWNER.hex() + ":domain:" + DOMAIN

VERIFIED, MISSING, UNREACHABLE = "verified", "missing", "unreachable"

# An EVM transfer, which is how value reaches a plain wallet.
EVM_PAY = "EmitExternalMessage"

# A proof checked a week before the tests' "now", so it starts fresh.
CHECKED = "2026-10-03T09:00:00Z"


class ProofStub:
    """
    Answers `get_claim` and `get_binding`, and records how it was asked.

    BOND reads both LINK and PROOF, so one stub serves both and dispatches
    on the method name, exactly as two separate contracts would.
    """

    def __init__(self):
        self.binding = {
            "binding_id": "0x" + "dd" * 20 + "|" + CLAIM,
            "owner": "0x" + OWNER.hex(),
            "kind": "domain",
            "resource": DOMAIN,
            "claim_id": CLAIM,
            "proof": "0x" + "dd" * 20,
            "verified_at": CHECKED,
            "adopted_at": "2026-10-03T10:00:00Z",
            "live": True,
            "withdrawn_at": "",
            "age_seconds": "604800",
            "fresh": True,
            "fresh_for_seconds": str(30 * 24 * 3600),
        }
        self.claim = {
            "claim_id": CLAIM,
            "owner": "0x" + OWNER.hex(),
            "kind": "domain",
            "resource": DOMAIN,
            "locator": "",
            "token": "genlayer-attest=0x" + OWNER.hex() + ":domain:" + DOMAIN,
            "url": "https://" + DOMAIN + "/.well-known/genlayer-attest.txt",
            "opened_at": "2026-10-01T09:00:00Z",
            "status": VERIFIED,
            "checked_at": CHECKED,
            "note": "the token is published at that domain",
        }
        self.calls = []
        self.transfers = []

    def set_claim(self, **changes):
        self.claim = {**self.claim, **changes}

    def set_binding(self, **changes):
        self.binding = {**self.binding, **changes}

    def install(self, vm):
        def hook(_vm, request):
            # Imported here rather than at install time: the SDK only exists
            # once a contract has been loaded, and the stub is installed first.
            from gltest.direct.sdk_compat import import_calldata

            calldata = import_calldata()

            for kind in ("PostMessage", "EthSend", EVM_PAY):
                if kind in request:
                    msg = request[kind]
                    self.transfers.append(
                        {"to": bytes(msg["address"].as_bytes), "value": int(msg["value"]), "kind": kind}
                    )
                    return {"ok": None}

            if "CallContract" not in request:
                return None
            call = request["CallContract"]
            method = call["calldata"][""]
            self.calls.append({"method": method, "storage_view": call.get("storage_view")})
            answer = self.binding if method == "get_binding" else self.claim
            return bytes([0]) + calldata.encode(answer)

        vm._gl_call_hook = hook
        return self
