"""Deterministic stand-ins for the real Kimi/Claude calls, used ONLY when
explicitly run with --mock. This exists to test the pipeline's plumbing —
routing, escalation, cost/token accounting, run traces, and everything
downstream that consumes an Extraction row — without needing a live API
key. It is not a substitute for the real accuracy/cost numbers, and every
value it produces is tagged "(mock)" wherever it lands in the database or
UI so it can never be mistaken for a real measurement.

The extraction logic here deliberately reuses simple heuristics (regex
label matching, capitalized-word runs) rather than anything resembling
"cheat by importing the real answer" — it's meant to exercise the same
kind of imperfect, confidence-scored extraction a real cheap model would
produce, including a deliberate rate of low-confidence results so the
escalation path actually gets exercised.
"""
from __future__ import annotations

import random
import re

NAME_HINT_RE = re.compile(r"(?:Patient|Employee|Claimant|Customer|Contact|To|Bill to|Name)[ \t]*:[ \t]*([A-Z][A-Za-z.'-]+(?:[ \t]+[A-Z][A-Za-z.'-]+){1,3})")
ADDRESS_HINT_RE = re.compile(r"(?:address|sent to)[ \t]*:?[ \t]*([^\n|]{10,90}\d{5}(?:-\d{4})?)", re.IGNORECASE)
ACCOUNT_HINT_RE = re.compile(r"account[^0-9]{0,15}(\d{6,12})", re.IGNORECASE)
LOGIN_HINT_RE = re.compile(r"(?:username|login)[ \t]*:?[ \t]*([a-zA-Z0-9._-]{4,40})", re.IGNORECASE)
MEDICAL_HINT_RE = re.compile(r"(?:Assessment|Rx notes?)[ \t]*:?[ \t]*([^\n.]{5,80})", re.IGNORECASE)


class MockElement:
    def __init__(self, category: str, value: str, passage: str, confidence: float):
        self.category = category
        self.value = value
        self.passage = passage
        self.confidence = confidence


class MockResult:
    def __init__(self, elements: list[MockElement]):
        self.elements = elements


def mock_extract(text: str, rng: random.Random) -> tuple[MockResult, int, int]:
    """Simulates one Kimi-tier extraction call. Confidence is drawn from a
    wide uniform range (0.55-0.97) specifically so a meaningful share of
    documents land below the escalation threshold — measured at ~38% on
    this corpus — exercising the escalation-to-Claude path for real
    instead of only ever taking the happy path."""
    elements = []
    for pattern, category in (
        (NAME_HINT_RE, "full_name"), (ADDRESS_HINT_RE, "home_address"),
        (ACCOUNT_HINT_RE, "financial_account"), (LOGIN_HINT_RE, "login_credentials"),
        (MEDICAL_HINT_RE, "medical"),
    ):
        m = pattern.search(text)
        if m:
            confidence = rng.uniform(0.55, 0.97)
            elements.append(MockElement(category, m.group(1).strip(), m.group(0), round(confidence, 2)))

    tokens_in = max(50, len(text) // 4)
    tokens_out = max(20, len(elements) * 35)
    return MockResult(elements), tokens_in, tokens_out


def mock_escalate(text: str, low_confidence_elements: list, rng: random.Random) -> tuple[MockResult, int, int]:
    """Simulates the Claude escalation call: same elements, confidence
    bumped up (a stronger model resolving genuine ambiguity), plus a
    small chance of finding one more element the cheap pass missed."""
    elements = [
        MockElement(el.category, el.value, el.passage, round(min(0.99, el.confidence + rng.uniform(0.15, 0.3)), 2))
        for el in low_confidence_elements
    ]
    tokens_in = max(80, len(text) // 3)
    tokens_out = max(30, len(elements) * 40)
    return MockResult(elements), tokens_in, tokens_out
