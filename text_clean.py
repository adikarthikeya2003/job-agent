"""Cleans raw job description text (HTML-escaped HTML, as returned by Greenhouse/Lever/etc.)
down to plain text before it hits the tagger or scorer — otherwise markup tokens and
boilerplate dilute the real keyword/semantic signal.
"""
import html
import re

from bs4 import BeautifulSoup

_TAG_RE = re.compile(r"<[^>]+>")


def clean_description(raw: str) -> str:
    if not raw:
        return ""
    unescaped = html.unescape(raw)
    if "<" in unescaped and ">" in unescaped:
        try:
            text = BeautifulSoup(unescaped, "lxml").get_text(separator=" ")
        except Exception:
            text = _TAG_RE.sub(" ", unescaped)
    else:
        text = unescaped
    text = re.sub(r"\s+", " ", text).strip()
    return text


# A "Preferred Qualifications" / "Nice to Have" section lists skills that are a bonus,
# not a bar to clear. Left in the scored text, its tokens still count toward the JD's
# TF-IDF vector: a resume that matches every REQUIRED qualification but none of the
# preferred ones sees its cosine similarity pulled down purely because the JD vector
# grew larger, not because the candidate is a worse fit. Stripped before scoring (not
# before display — the full description is still shown/used for experience tagging).
_PREFERRED_HEADER_RE = re.compile(
    r"(preferred|desired)\s+(qualifications|skills|experience|requirements)|"
    r"nice[\s-]to[\s-]haves?|bonus\s+points?|good\s+to\s+have|"
    r"it'?s\s+a\s+plus\s+if|would\s+be\s+a\s+plus|extra\s+credit",
    re.I,
)
# Where a preferred-quals section ends: the next real section header, or a hard cap on
# span length so a JD with no recognizable next header doesn't get eaten wholesale.
_SECTION_END_RE = re.compile(
    r"required\s+qualifications|minimum\s+qualifications|basic\s+qualifications|"
    r"responsibilities|what\s+you.?ll\s+do|what\s+you\s+bring|who\s+you\s+are|"
    r"about\s+(the\s+)?(role|team|us|company)|what\s+we\s+offer|benefits|"
    r"compensation|equal\s+opportunity|how\s+to\s+apply|perks",
    re.I,
)
_MAX_PREFERRED_SPAN = 700  # chars; a real "preferred" section rarely runs longer


def strip_preferred_section(text: str) -> str:
    """Removes 'Preferred Qualifications' / 'Nice to have' style sections from JD text
    used for scoring. Safe to call on already-cleaned (whitespace-collapsed) text."""
    if not text:
        return text
    out = []
    pos = 0
    for m in _PREFERRED_HEADER_RE.finditer(text):
        if m.start() < pos:
            continue  # already consumed by a prior cut
        out.append(text[pos:m.start()])
        cap = m.end() + _MAX_PREFERRED_SPAN
        end_m = _SECTION_END_RE.search(text, m.end(), cap)
        pos = end_m.start() if end_m else min(cap, len(text))
    out.append(text[pos:])
    return re.sub(r"\s+", " ", "".join(out)).strip()
