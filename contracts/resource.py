"""
The pure part of ATTEST. No chain, no network, no model.

Everything that turns a fetched document into "this address controls this
resource" lives here, where it can be tested directly and read by anyone
checking what consensus actually compared.

The three outcomes are the point of the design, and they are three different
claims about the world:

    verified     the document was fetched and the token was in it
    missing      the document was fetched and the token was not in it
    unreachable  the document could not be fetched at all

Collapsing `missing` into `unreachable` would be a disaster here rather than
merely untidy. A bond is slashed when a proof goes `missing`, so a source
that is briefly down must never look like a claim that was withdrawn. One of
those is a statement about the claimant, the other is a statement about the
network, and only the first may cost anybody money.
"""

VERIFIED = "verified"
MISSING = "missing"
UNREACHABLE = "unreachable"

# Where a domain publishes its proof. Fixed by the contract, never supplied
# by a caller: a claimant who chooses the path chooses the evidence.
WELL_KNOWN = "/.well-known/genlayer-attest.txt"

# A gist is fetched through the API rather than the HTML page, because the
# API answers JSON and names the owner, and the owner is the whole point.
GIST_HOST = "api.github.com"

_MAX_DOMAIN = 100
_MAX_HANDLE = 39  # GitHub's own limit
_MAX_GIST = 64


def valid_domain(domain: str) -> bool:
    """
    A bare hostname: letters, digits, dots and hyphens.

    No scheme, no slash, no port, no credentials, no userinfo. The contract
    builds the URL itself, so anything that could smuggle a second host or a
    path out of this value is refused when the claim is opened.
    """
    d = (domain or "").strip().lower()
    if not d or len(d) > _MAX_DOMAIN or d.startswith(".") or d.endswith(".") or ".." in d:
        return False
    if d.startswith("-") or d.endswith("-"):
        return False
    for ch in d:
        if not (("0" <= ch <= "9") or ("a" <= ch <= "z") or ch in "-."):
            return False
    # A single label is not a domain anybody can own publicly.
    return "." in d


def valid_handle(handle: str) -> bool:
    """
    A GitHub username: alphanumeric or single hyphens, not leading or
    trailing, at most 39 characters. GitHub's own rule, enforced here so a
    claim can never be opened on a name that cannot exist.
    """
    h = (handle or "").strip()
    if not h or len(h) > _MAX_HANDLE:
        return False
    if h.startswith("-") or h.endswith("-") or "--" in h:
        return False
    for ch in h:
        if not (("0" <= ch <= "9") or ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ch == "-"):
            return False
    return True


def valid_gist_id(gist_id: str) -> bool:
    """A gist id as GitHub issues them: hex, and nothing that could leave the path."""
    g = (gist_id or "").strip()
    if not g or len(g) > _MAX_GIST:
        return False
    for ch in g:
        if not (("0" <= ch <= "9") or ("a" <= ch <= "f") or ("A" <= ch <= "F")):
            return False
    return True


def token_for(owner_hex: str, kind: str, resource: str) -> str:
    """
    The exact line a claimant must publish.

    Derived from the claiming address and the resource, and from nothing
    else. Deliberately not from the time: a token that changed on every
    renewal would mean editing the published file forever, and the whole
    value of this is that a proof can be re-checked years later without the
    claimant doing anything.

    It does not need to be unguessable. Knowing somebody else's token buys
    nothing, because publishing it requires controlling the resource, which
    is the only thing being proven.
    """
    return "genlayer-attest=" + owner_hex.strip().lower() + ":" + kind + ":" + resource.strip().lower()


def domain_url(domain: str) -> str:
    """The one URL a domain claim is ever read from."""
    return "https://" + domain.strip().lower() + WELL_KNOWN


def gist_url(gist_id: str) -> str:
    """The one URL a handle claim is ever read from."""
    return "https://" + GIST_HOST + "/gists/" + gist_id.strip()


def token_present(body: str, token: str) -> bool:
    """
    Whether the published document carries the token on a line of its own.

    Line by line and exact after trimming, rather than a substring search of
    the whole body. A substring match would let an attacker get a proof
    accepted by persuading a victim's site to echo arbitrary text, which
    plenty of sites do in error pages and search results.
    """
    if not token:
        return False
    for line in (body or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.strip() == token:
            return True
    return False


def gist_owner(document) -> str:
    """
    The login that owns a gist, from the API's JSON, or "".

    This is what makes a handle claim mean anything: the token proves someone
    wrote the file, and the owner proves who.
    """
    if not isinstance(document, dict):
        return ""
    owner = document.get("owner")
    if not isinstance(owner, dict):
        return ""
    login = owner.get("login")
    return login if isinstance(login, str) else ""


def gist_text(document) -> str:
    """
    Every file in a gist, joined, so the token can be on a line in any of them.

    Truncated files are skipped rather than half-read: GitHub sets `truncated`
    when it did not send the whole thing, and half a document is not evidence.
    """
    if not isinstance(document, dict):
        return ""
    files = document.get("files")
    if not isinstance(files, dict):
        return ""
    parts = []
    for name in sorted(files):
        entry = files[name]
        if not isinstance(entry, dict) or entry.get("truncated") is True:
            continue
        content = entry.get("content")
        if isinstance(content, str):
            parts.append(content)
    return "\n".join(parts)


def same_handle(a: str, b: str) -> bool:
    """GitHub logins are case insensitive, and a claim must not turn on case."""
    return (a or "").strip().lower() == (b or "").strip().lower()


# --------------------------------------------------------------------
# dates
#
# A proof goes stale. Checking whether it has means subtracting two days,
# and the contract cannot import a date library, so the arithmetic is here
# and the tests check it against the standard library.
# --------------------------------------------------------------------

def epoch_seconds(stamp: str):
    """
    An ISO-8601 UTC timestamp as a whole number of seconds, or None.

    Only the fixed "YYYY-MM-DDTHH:MM:SS" prefix is read, so a stamp carrying
    fractional seconds is accepted and truncated rather than refused: the
    network writes "2026-10-02T14:42:50.790497Z" and a test writes
    "2026-10-02T14:42:50Z", and those have to mean the same instant.

    Returns None for anything it cannot read, which callers must treat as
    "cannot decide" rather than as zero. Comparing timestamps as text very
    nearly works and then does not, because "Z" sorts after ".".
    """
    s = (stamp or "").strip()
    if len(s) < 19 or s[4] != "-" or s[7] != "-" or s[10] != "T" or s[13] != ":" or s[16] != ":":
        return None
    for i in (0, 1, 2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 17, 18):
        if not ("0" <= s[i] <= "9"):
            return None
    year, month, day = int(s[0:4]), int(s[5:7]), int(s[8:10])
    hour, minute, second = int(s[11:13]), int(s[14:16]), int(s[17:19])
    if not (1 <= month <= 12) or day < 1 or day > _days_in_month(year, month):
        return None
    # 60 is allowed because an ISO-8601 stamp may carry a leap second.
    if hour > 23 or minute > 59 or second > 60:
        return None
    return _days_from_civil(year, month, day) * 86400 + hour * 3600 + minute * 60 + second


def _days_in_month(year: int, month: int) -> int:
    """So an impossible date is refused rather than rolling into the next month."""
    if month == 2:
        leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        return 29 if leap else 28
    return 30 if month in (4, 6, 9, 11) else 31


def _days_from_civil(year: int, month: int, day: int) -> int:
    """
    Days since 1970-01-01, by the usual era arithmetic.

    Integer only and no library, because the same number has to come out on
    every validator. The calendar is the proleptic Gregorian one, which is
    what an ISO-8601 stamp means.
    """
    y = year - (1 if month <= 2 else 0)
    era = (y if y >= 0 else y - 399) // 400
    year_of_era = y - era * 400
    day_of_year = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    day_of_era = year_of_era * 365 + year_of_era // 4 - year_of_era // 100 + day_of_year
    return era * 146097 + day_of_era - 719468


def end_of_day(day: str):
    """
    The last second of a calendar day, as epoch seconds, or None.

    A cover period is given in whole days, and the question "was this read
    inside the period" has to be asked against a precise instant.
    """
    stamp = (day or "").strip()
    if len(stamp) != 10:
        return None
    return epoch_seconds(stamp + "T23:59:59Z")


def next_day(day: str):
    """
    The calendar day after this one, as YYYY-MM-DD, or None.

    Used once, to derive a deal's claim deadline from its cover period, so
    the depositor never has to leave headroom by hand.
    """
    at = epoch_seconds((day or "").strip() + "T12:00:00Z")
    if at is None:
        return None
    return _civil_from_days(at // 86400 + 1)


def _civil_from_days(days: int) -> str:
    """
    The inverse of _days_from_civil, by the same era arithmetic.

    Integer only and no library, because every validator has to produce the
    same string.
    """
    z = days + 719468
    era = (z if z >= 0 else z - 146096) // 146097
    day_of_era = z - era * 146097
    year_of_era = (day_of_era - day_of_era // 1460 + day_of_era // 36524 - day_of_era // 146096) // 365
    year = year_of_era + era * 400
    day_of_year = day_of_era - (365 * year_of_era + year_of_era // 4 - year_of_era // 100)
    mp = (5 * day_of_year + 2) // 153
    day = day_of_year - (153 * mp + 2) // 5 + 1
    month = mp + (3 if mp < 10 else -9)
    if month <= 2:
        year += 1
    return f"{year:04d}-{month:02d}-{day:02d}"
