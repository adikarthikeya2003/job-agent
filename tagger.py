"""Tags a raw job posting with role family, seniority, remote status, and sponsorship flag.
All tagging is keyword-based against title + description text — no ML, fully traceable.
"""
import re

_US_STATE_ABBR = set("""AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN
MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC""".split())

_US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa",
    "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan",
    "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island",
    "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont",
    "virginia", "washington", "west virginia", "wisconsin", "wyoming",
    "district of columbia", "puerto rico",
}

_US_COUNTRY_MARKERS = ["united states", "usa", "u.s.a", "u.s.", " us ", "us-remote",
                        "remote - usa", "remote, usa", "remote-us"]

# Checked only when no explicit US marker is present. A posting naming one of these and
# no US signal is non-US. Deliberately omits "georgia" (also a US state) — that case is
# resolved by the US-marker and state-name checks instead.
_NON_US_COUNTRIES = {
    "france", "germany", "india", "china", "japan", "korea", "south korea", "united kingdom",
    "england", "scotland", "ireland", "canada", "mexico", "brazil", "argentina", "chile",
    "colombia", "peru", "tunisia", "morocco", "egypt", "israel", "turkey", "poland",
    "czech republic", "hungary", "romania", "russia", "ukraine", "netherlands", "belgium",
    "spain", "italy", "portugal", "greece", "sweden", "norway", "denmark", "finland",
    "switzerland", "austria", "singapore", "australia", "new zealand", "vietnam",
    "philippines", "indonesia", "thailand", "malaysia", "taiwan", "hong kong", "pakistan",
    "bangladesh", "sri lanka", "nigeria", "kenya", "south africa", "united arab emirates",
    "saudi arabia", "qatar", "dubai",
}


def is_us_location(location: str, remote_status: str = "") -> bool:
    """True if the posting is explicitly US-based, including US-remote.

    Fails closed: a posting with no location signal at all is treated as NOT US rather
    than assumed US, since ambiguity here has real consequences (OPT/visa).

    Resolution order matters:
      1. An explicit US marker wins outright, even alongside other countries — a
         "Remote - US/Canada" posting is US-eligible.
      2. Otherwise a named non-US country disqualifies it.
      3. State abbreviations are matched POSITIONALLY (only in the last two
         comma-separated segments) and CASE-SENSITIVELY, because bare two-letter words
         are common in foreign street addresses: "Centre d'affaires la Boursidière,
         Le Plessis-Robinson, France" would otherwise match "la" -> LA -> Louisiana.
    """
    if not location:
        return False
    low = location.lower()
    # Treat slashes/pipes as word boundaries so "Remote - US/Canada" exposes a bare "us".
    text = " " + re.sub(r"[/|]+", " ", low) + " "

    for marker in _US_COUNTRY_MARKERS:
        if marker in text:
            return True

    for country in _NON_US_COUNTRIES:
        if country in low:
            return False

    # Only the trailing segments of an address carry the state, e.g.
    # "3900 N Capital of Texas Hwy, Austin, TX" -> consider "Austin" / "TX".
    segments = [s.strip() for s in location.split(",") if s.strip()]
    tail = segments[-2:] if len(segments) >= 2 else segments

    for seg in tail:
        # Case-sensitive: a real state abbreviation is uppercase in the source string.
        for token in re.split(r"[\s/]+", seg):
            token = token.strip(".")
            if token in _US_STATE_ABBR:
                return True
        if seg.lower() in _US_STATE_NAMES:
            return True

    return False


def tag_role_family(title: str, description: str, families_cfg: dict) -> str:
    text = f"{title} {description[:1000]}".lower()
    for family, keywords in families_cfg.items():
        for kw in keywords:
            if kw.lower() in text:
                return family
    return "unclassified"


def tag_seniority(title: str, description: str, seniority_cfg: dict) -> str:
    text = f"{title} {description[:500]}".lower()
    for level in ("senior", "mid", "entry"):
        for kw in seniority_cfg.get(level, []):
            if kw.lower() in text:
                return level
    return "unspecified"


_REMOTE_RE = re.compile(r"\bremote\b", re.I)
_HYBRID_RE = re.compile(r"\bhybrid\b", re.I)
_ONSITE_RE = re.compile(r"\bon[- ]?site\b", re.I)


def tag_remote_status(title: str, description: str, location: str) -> str:
    text = f"{title} {description} {location}"
    if _REMOTE_RE.search(text):
        return "remote"
    if _HYBRID_RE.search(text):
        return "hybrid"
    if _ONSITE_RE.search(text):
        return "onsite"
    return "unspecified"


def tag_sponsorship(description: str, sponsorship_cfg: dict) -> str:
    text = description.lower()
    for phrase in sponsorship_cfg.get("exclude_phrases", []):
        if phrase.lower() in text:
            return "EXCLUDED"
    for phrase in sponsorship_cfg.get("include_phrases", []):
        if phrase.lower() in text:
            return "sponsors"
    return "unstated"


_WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fifteen": 15, "twenty": 20,
}
_NUM_WORDS_RE = "|".join(_WORD_NUMBERS)

# "5+ years", "3-5 years", "2 to 4 yrs", "five years", "minimum of 8 years".
_YEARS_RE = re.compile(
    r"(?P<lo>\d{1,2}|" + _NUM_WORDS_RE + r")\s*"
    r"(?:\+|plus)?\s*"
    r"(?:(?:-|–|—|to)\s*(?P<hi>\d{1,2})\s*)?"
    r"(?:\+\s*)?"
    r"(?:years?|yrs?)\b",
    re.I,
)

# A years-figure only counts as a requirement if "experience" appears near it — a JD
# saying "our platform has served customers for 10 years" is not a hiring bar.
_EXPERIENCE_NEAR_RE = re.compile(r"experien|background|track record", re.I)

_SENIOR_TITLE_RE = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|director|manager|head of|architect|vp)\b", re.I)

_NEW_GRAD_RE = re.compile(
    r"new grad|recent graduate|university graduate|entry[- ]level|early career|"
    r"no (?:prior )?experience (?:is )?(?:required|necessary)|graduating|intern(?:ship)?\b",
    re.I,
)


def _to_int(token: str):
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return _WORD_NUMBERS.get(token)


def tag_experience_required(title: str, description: str) -> str:
    """Extract the years-of-experience bar the posting itself states.

    Returns a short human-readable string ("5+ yrs", "3-5 yrs", "0-2 yrs (new grad)")
    or "not stated". Never invents a number: sources that publish no JD body (Samsung,
    speedyapply, Google cards) legitimately come back "not stated" unless the title
    itself carries a signal.
    """
    text = f"{title}. {description}"
    if not text.strip():
        return "not stated"

    lows, his = [], []
    for m in _YEARS_RE.finditer(text):
        # Require an experience-ish word within ~60 chars either side of the match.
        window = text[max(0, m.start() - 60):m.end() + 60]
        if not _EXPERIENCE_NEAR_RE.search(window):
            continue
        lo = _to_int(m.group("lo"))
        if lo is None or lo > 40:          # guard against parsing a stray year like "2026"
            continue
        hi = _to_int(m.group("hi") or "") if m.group("hi") else None
        lows.append(lo)
        if hi is not None and hi >= lo:
            his.append(hi)

    senior_title = _SENIOR_TITLE_RE.search(title or "")

    if lows:
        lo = min(lows)
        # A req that spells out several distinct bands ("Entry Level (0-2 years) ...
        # Mid (2-5 years)") is a multi-level posting. Reporting only its lowest band
        # reads as "0-2 yrs" on a Sr. role, which is worse than saying nothing — so
        # report the full span instead.
        if len(set(lows)) >= 2:
            top = max(his + lows)
            # "(multi-level)" only when the bands actually start at entry — otherwise
            # two figures usually just mean required-vs-preferred ("4 years required,
            # 6 to 8 preferred"), which the plain span already conveys.
            span = f"{lo}-{top} yrs"
            return f"{span} (multi-level)" if lo <= 2 and _NEW_GRAD_RE.search(text) else span

        # Only report an upper bound when the posting stated an explicit range for the
        # same (minimum) bar — otherwise "5+ yrs" is the honest reading.
        hi = min(his) if his and min(his) > lo else None
        label = f"{lo}-{hi} yrs" if hi else f"{lo}+ yrs"
        if lo == 0:
            label = f"0-{hi} yrs" if hi else "0+ yrs"
        if _NEW_GRAD_RE.search(text) and lo <= 2 and not senior_title:
            label += " (new grad)"
        return label

    # No number anywhere: a new-grad signal is only trustworthy if the title doesn't
    # contradict it (senior JDs mention internship programs in boilerplate).
    if _NEW_GRAD_RE.search(text) and not senior_title:
        return "0-2 yrs (new grad)"
    return "not stated"


def tag_job(job: dict, roles_cfg: dict, sponsorship_cfg: dict) -> dict:
    """job: dict with at least title, description, location keys. Returns job with
    role_family, seniority, remote_status, sponsorship_flag, experience_required
    added in place."""
    title = job.get("title", "")
    description = job.get("description", "")
    location = job.get("location", "")
    job["role_family"] = tag_role_family(title, description, roles_cfg.get("families", {}))
    job["seniority"] = tag_seniority(title, description, roles_cfg.get("seniority", {}))
    job["remote_status"] = tag_remote_status(title, description, location)
    job["sponsorship_flag"] = tag_sponsorship(description, sponsorship_cfg)
    job["experience_required"] = tag_experience_required(title, description)
    job["is_us"] = is_us_location(location, job["remote_status"])
    return job


def experience_min_years(label: str):
    """Numeric sort key for an `experience_required` label, or None if not stated.

    "3+ yrs" -> 3, "0-5 yrs (multi-level)" -> 0, "not stated" -> None. Sorting on the
    LOWER bound is what matters: it's the bar you have to clear to be considered.
    """
    if not label or label == "not stated":
        return None
    m = re.match(r"\s*(\d{1,2})", label)
    return int(m.group(1)) if m else None
