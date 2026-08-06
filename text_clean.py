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
