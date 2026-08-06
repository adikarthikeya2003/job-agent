"""Shared fetcher utilities: robots.txt gate, HTTP session, result envelope."""
import re
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests

USER_AGENT = "job-agent/1.0 (personal job search tool; contact: adikarthikeya1234@gmail.com)"


@dataclass
class FetchResult:
    company: str
    tier_used: int
    source: str
    ok: bool
    jobs: list = field(default_factory=list)
    error: str = ""
    manual_only: bool = False


def _rule_matches(path: str, pattern: str) -> int:
    """Return the match length if `pattern` matches `path`, else -1.

    Supports the two robots.txt wildcards: `*` (any sequence) and a trailing `$`
    (end-of-path anchor). Match length is the pattern's specificity, used for
    longest-match precedence.
    """
    if not pattern:
        return -1
    anchored = pattern.endswith("$")
    pat = pattern[:-1] if anchored else pattern
    if "*" in pat:
        regex = "".join(re.escape(part) for part in pat.split("*"))
        regex = ".*".join(re.escape(p) for p in pat.split("*"))
        regex = "^" + regex + ("$" if anchored else "")
        return len(pat) if re.match(regex, path) else -1
    if anchored:
        return len(pat) if path == pat else -1
    return len(pat) if path.startswith(pat) else -1


def _can_fetch(robots_text: str, url: str, user_agent: str) -> bool:
    """Spec-correct (RFC 9309) robots.txt evaluation with LONGEST-match precedence.

    Python's stdlib `urllib.robotparser` applies FIRST-match precedence, which gets
    real sites wrong. Microsoft's careers robots.txt is the motivating case:

        User-agent: *
        Disallow: /
        Allow: /careers
        Allow: /api/pcsx

    The stdlib parser sees `Disallow: /` first and refuses everything, even though the
    site explicitly permits `/api/pcsx`. Under the actual standard the longest matching
    rule wins, and Allow wins ties — so `/api/pcsx/search` is permitted.

    Still fails closed everywhere it matters: an unreadable robots.txt, or a path with
    no matching Allow but a matching Disallow, is treated as disallowed.
    """
    groups, current_agents, active = {}, [], False
    for raw_line in robots_text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            if not active:            # consecutive user-agent lines share one group
                current_agents = []
            current_agents.append(value.lower())
            active = True
            for agent in current_agents:
                groups.setdefault(agent, [])
        elif field in ("allow", "disallow"):
            active = False
            for agent in current_agents:
                groups.setdefault(agent, []).append((field, value))

    ua_token = user_agent.split("/", 1)[0].strip().lower()
    rules = None
    for agent, agent_rules in groups.items():
        if agent != "*" and agent in ua_token:
            rules = agent_rules
            break
    if rules is None:
        rules = groups.get("*")
    if rules is None:
        return True                    # no applicable group — permitted by convention

    path = urlparse(url).path or "/"
    best_allow, best_disallow = -1, -1
    for kind, pattern in rules:
        length = _rule_matches(path, pattern)
        if length < 0:
            continue
        if kind == "allow":
            best_allow = max(best_allow, length)
        else:
            # An empty Disallow value means "allow everything" — never a match.
            if pattern:
                best_disallow = max(best_disallow, length)

    if best_disallow < 0:
        return True
    return best_allow >= best_disallow


def robots_allows(url: str, user_agent: str = USER_AGENT) -> bool:
    """Checks the target site's robots.txt before any Tier 3 scrape. Fails closed:
    if robots.txt can't be fetched/parsed, treat as disallowed rather than assume
    permission."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        resp = requests.get(robots_url, headers={"User-Agent": user_agent}, timeout=10)
        if resp.status_code >= 400:
            # No robots.txt published — permissive by convention.
            return True
    except requests.RequestException:
        return False
    return _can_fetch(resp.text, url, user_agent)


RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SEC = (1, 3)          # waits between attempt 1->2 and 2->3
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _request_with_retry(method: str, url: str, **kwargs) -> requests.Response:
    """Issue a request, retrying transient failures.

    Employer careers APIs fail intermittently in ways that are not real errors: TLS
    handshake alerts, read timeouts mid-pagination, and 429/5xx under load. Observed
    live — IBM returned `TLSV1_ALERT_INTERNAL_ERROR` on one run and succeeded on the
    next with no change. Without retries a whole company silently drops out of a fetch.

    Only transient classes are retried. A 404 or 422 is a real answer about a wrong
    endpoint or bad parameters and is returned immediately, so genuine breakage still
    surfaces as a visible FAIL instead of being masked by three slow attempts.
    """
    last_exc = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code in RETRYABLE_STATUS and attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_BACKOFF_SEC[attempt])
                continue
            return resp
        except (requests.exceptions.SSLError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            last_exc = e
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_BACKOFF_SEC[attempt])
                continue
            raise
    if last_exc:
        raise last_exc
    raise requests.RequestException("retry loop exhausted without a response")


def http_get(url: str, **kwargs) -> requests.Response:
    headers = kwargs.pop("headers", {})
    headers.setdefault("User-Agent", USER_AGENT)
    return _request_with_retry("GET", url, headers=headers,
                               timeout=kwargs.pop("timeout", 20), **kwargs)


def http_post(url: str, **kwargs) -> requests.Response:
    headers = kwargs.pop("headers", {})
    headers.setdefault("User-Agent", USER_AGENT)
    headers.setdefault("Content-Type", "application/json")
    return _request_with_retry("POST", url, headers=headers,
                               timeout=kwargs.pop("timeout", 20), **kwargs)
