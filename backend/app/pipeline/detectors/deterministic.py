"""Deterministic PII detectors: regex + checksum validation, zero LLM cost.

These run first in the cost-tiered pipeline (brief section 6) and cover the
PII categories that have a fixed, checkable shape. Context-dependent
categories (full name, home address, medical notes, login credentials,
free-text financial references) are NOT reliably detectable this way and
are left to the LLM tier — see app/pipeline/detectors/llm.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

SSN_RE = re.compile(r"\b(\d{3})-(\d{2})-(\d{4})\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# The digit boundaries are load-bearing, not tidiness.
#
# Without them this pattern matches ANY ten consecutive digits, so it
# fired inside card numbers: card 9999794026542359 produced a "phone" of
# 9999794026, its own first ten digits. Measured on the realistic
# corpus, 156 of 461 distinct phone values were substrings of a card
# number and 27.9% of all phone extractions were not a phone-shaped
# length at all.
#
# That is worse than an ordinary false positive because phone is a
# STRONG IDENTITY KEY in entity resolution: a phantom number invented
# from someone's card digits can join two unrelated people into one
# identity. It survived this long because the accuracy scorer measures
# whether a person has a phone flag, not whether the value is right — a
# person with one real phone and three phantoms still scored as a clean
# true positive.
PHONE_RE = re.compile(
    r"(?<!\d)"                                              # not mid-run
    r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"
    r"(?:\s?x\d+)?"
    r"(?!\d)"                                               # nor a prefix of one
)
DOB_ISO_RE = re.compile(r"\b(19|20)\d{2}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b")
CARD_CANDIDATE_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
DRIVERS_LICENSE_RE = re.compile(r"\b[A-Z]\d{8}\b")
# Regression test: the corpus's actual phrasing is "Passport on file for
# identity verification (travel medication):\n<9 digits>" -- ~57
# non-digit characters between the word and the number, well past a
# 20-char window. This is why passport recall was 0% all session
# regardless of which LLM provider was tried: the deterministic detector
# never matched, AND passport wasn't in the LLM tier's category list
# either (see LLM_CATEGORIES in app/services/llm_extraction.py) -- two
# independent gaps, not a model capability limit.
PASSPORT_CONTEXT_RE = re.compile(r"passport[^0-9]{0,100}(\d{9})", re.IGNORECASE)

# SSN order-number false-positive suppression: text explicitly disclaiming
# the number is a tracking/order reference, not a government identifier.
FALSE_POSITIVE_CONTEXT_RE = re.compile(
    r"(order|invoice|purchase|tracking|internal)[^.]{0,40}(reference|number|ref)", re.IGNORECASE
)

# The rule above needs BOTH a trigger word and the word "reference" or
# "number" nearby, which misses the most common phrasing of all: the
# reference word standing alone right before the value. "Invoice — Order
# 755-98-4598" and a spreadsheet cell reading "Order Ref: 755-98-4598"
# both name the thing and then give it, with nothing else in between.
#
# Checked against the text IMMEDIATELY preceding the match rather than
# the whole passage, for the same reason the phone context check is:
# a window wide enough to catch a stray "order" elsewhere in the row
# would suppress real SSNs sitting beside an order column.
_SSN_DECOY_LEAD_IN_RE = re.compile(
    r"\b(order|invoice|purchase|tracking|internal|po|ref|reference|"
    r"confirmation|transaction|case|claim|policy|batch|job)\b"
    r"[^0-9A-Za-z]{0,4}(?:no\.?|num\.?|number|ref\.?|#|id)?[^0-9A-Za-z]{0,4}$",
    re.IGNORECASE,
)


@dataclass
class Hit:
    category: str
    raw_value: str
    normalized_value: str
    passage: str
    confidence: float
    method: str  # "deterministic_regex" | "deterministic_checksum"
    is_partial: bool = False
    suppressed_as_false_positive: bool = False
    # Character offsets of the match within the record text. Every
    # detector already computes these (m.start()/m.end()); carrying them
    # is what lets the UI say "page 3" instead of "somewhere in this
    # 40-page PDF". -1 means the detector did not report a position.
    char_start: int = -1
    char_end: int = -1


def _passage_around(text: str, start: int, end: int, window: int = 60) -> str:
    return text[max(0, start - window): min(len(text), end + window)].strip().replace("\n", " ")


def _luhn_ok(digits: str) -> bool:
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def detect_ssn(text: str) -> list[Hit]:
    hits = []
    for m in SSN_RE.finditer(text):
        raw = m.group(0)
        passage = _passage_around(text, m.start(), m.end())
        lead_in = text[max(0, m.start() - 40): m.start()]
        suppressed = bool(FALSE_POSITIVE_CONTEXT_RE.search(passage)
                           or _SSN_DECOY_LEAD_IN_RE.search(lead_in))
        # Known QA-fixture placeholder must never surface as real.
        if raw == "000-00-0000":
            suppressed = True
        hits.append(Hit("ssn", raw, raw, passage, confidence=0.97 if not suppressed else 0.05,
                         method="deterministic_regex", suppressed_as_false_positive=suppressed, char_start=m.start(), char_end=m.end()))
    return hits


def detect_ssn_last4(text: str) -> list[Hit]:
    """xxx-xx-1234 style partial SSN references."""
    pattern = re.compile(r"xxx-xx-(\d{4})", re.IGNORECASE)
    hits = []
    for m in pattern.finditer(text):
        passage = _passage_around(text, m.start(), m.end())
        hits.append(Hit("ssn", m.group(1), m.group(1), passage, confidence=0.6,
                         method="deterministic_regex", is_partial=True, char_start=m.start(), char_end=m.end()))
    return hits


def detect_email(text: str) -> list[Hit]:
    hits = []
    for m in EMAIL_RE.finditer(text):
        raw = m.group(0)
        passage = _passage_around(text, m.start(), m.end())
        is_shared_mailbox = raw.split("@")[0].lower() in set(_profile().shared_mailboxes)
        hits.append(Hit("email", raw, raw.lower(), passage, confidence=0.95 if not is_shared_mailbox else 0.1,
                         method="deterministic_regex", suppressed_as_false_positive=is_shared_mailbox, char_start=m.start(), char_end=m.end()))
    return hits


# Written like a phone number: brackets, dashes, dots or spaces between
# the groups, or an explicit country code.
_PHONE_PUNCTUATED_RE = re.compile(r"[()\-.\s]|^\+")
# ...or described as one by the words around it.
_PHONE_CONTEXT_RE = re.compile(
    r"\b(phone|tel|telephone|mobile|cell|call|called|direct|fax|contact|"
    r"reached|reach|voicemail|hotline|extension)\b",
    re.IGNORECASE,
)


def normalize_phone(raw: str) -> str:
    """Canonical phone form: digits only, with the country-code prefix
    stripped so the same number written different ways compares equal.

    Measured reason this matters: the same person's phone appears as
    "001-406-574-4716x3928" in one document and "1-406-574-4716x3928" in
    another (the regex's optional prefix doesn't span "00"). Without
    canonicalization those normalize to different strings, and the entity
    resolver treats one person's phone as two — which is exactly what
    blocked phone from being usable as a strong identity key.
    """
    # Drop any extension first. "402.907.0160x166" is one subscriber
    # number, not a 13-digit one, and counting the extension digits both
    # inflated the length check and made the same line normalize
    # differently depending on whether the extension was written.
    raw = re.split(r"\s?(?:x|ext\.?)\s?\d+\s*$", raw, flags=re.IGNORECASE)[0]

    digits = re.sub(r"\D", "", raw)
    if digits.startswith("00"):
        digits = digits[2:]
    # A leading US country code only, never a real area code's leading 1
    # (NANP area codes never start with 0 or 1), so this is unambiguous.
    if len(digits) > 10 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def detect_phone(text: str) -> list[Hit]:
    hits = []
    for m in PHONE_RE.finditer(text):
        raw = m.group(0)
        digits = normalize_phone(raw)
        # Exactly a subscriber number, once the country code and any
        # extension are removed. A "< 10" floor let 13- and 15-digit runs
        # through as phones.
        if len(digits) not in (10, 11):
            continue

        passage = _passage_around(text, m.start(), m.end())

        # A bare run of ten digits is not evidence of a phone number.
        # Bank account numbers in this corpus are exactly ten digits, so
        # the pattern matched them perfectly: 5877639950 was extracted as
        # both a phone and an account, and 310 of 921 linked phone values
        # were not phone numbers at all.
        #
        # A real phone number is almost always PUNCTUATED — (216)
        # 555-0113, 402.907.0160, 606-555-0142 — because that is how
        # people write them. An unpunctuated run therefore has to earn it
        # from the surrounding words instead.
        # The context word has to be IMMEDIATELY before the number, not
        # merely somewhere in the surrounding passage. A ±60-character
        # window is wide enough to catch an unrelated "contact" or
        # "call" elsewhere in the sentence, which is how account numbers
        # kept qualifying: "Any refund due is to be paid to account
        # 5877639950" sits close enough to a "call back" clause to pass a
        # passage-wide test.
        lead_in = text[max(0, m.start() - 24): m.start()]
        if not _PHONE_PUNCTUATED_RE.search(raw) and not _PHONE_CONTEXT_RE.search(lead_in):
            continue

        hits.append(Hit("phone", raw, digits, passage, confidence=0.85, method="deterministic_regex", char_start=m.start(), char_end=m.end()))
    return hits


# A date of birth is not just a well-formed date.
#
# Measured: 622 of 1401 extracted "DOB" values were not birth dates at
# all. They were the ordinary dates every business document is full of —
# a legal bundle's exhibit index, a bank statement's transaction column,
# a letter's issue date. Each was scored 0.4 confidence and still became
# a dob exposure flag on somebody's record, because the accuracy scorer
# only ever asked whether the person HAD a dob flag, never whether the
# date was theirs.
#
# Two rules, both about what a birth date can be rather than what it
# looks like. A date in the future is impossible. A date inside the last
# _MIN_PLAUSIBLE_AGE years belongs to someone too young to appear in
# these records as a data subject, so it needs the word "birth" or "DOB"
# nearby before it is believed — which is exactly the boundary between a
# statement date and a stated date of birth.
_MIN_PLAUSIBLE_AGE = 16
_MAX_PLAUSIBLE_AGE = 120


def _dob_is_plausible(iso: str, has_context: bool) -> bool:
    try:
        year, month, day = (int(p) for p in iso.split("-"))
        born = date(year, month, day)
    except ValueError:
        return False

    today = date.today()
    if born > today:
        return False                      # nobody is born in the future
    age = (today - born).days / 365.2425
    if age > _MAX_PLAUSIBLE_AGE:
        return False
    if age < _MIN_PLAUSIBLE_AGE:
        # Recent enough to be a document date; only believe it if the
        # document says it is a birth date.
        return has_context
    return True


def detect_dob(text: str) -> list[Hit]:
    hits = []
    for m in DOB_ISO_RE.finditer(text):
        raw = m.group(0)
        passage = _passage_around(text, m.start(), m.end())
        lowered = passage.lower()
        has_context = "dob" in lowered or "birth" in lowered or "born" in lowered
        if not _dob_is_plausible(raw, has_context):
            continue
        hits.append(Hit("dob", raw, raw, passage, confidence=0.9 if has_context else 0.4,
                         method="deterministic_regex", char_start=m.start(), char_end=m.end()))
    return hits


# Well-known payment-processor test/sandbox card numbers. These are
# Luhn-valid by design (so integration tests can exercise real checksum
# logic) but are never a real cardholder's number — found in production
# on this corpus via the QA-fixture false-positive trap document, which
# plants exactly this number ("4111 1111 1111 1111") to test whether
# extraction blindly trusts the checksum or knows the industry-standard
# test values. Luhn validity alone is necessary but not sufficient.
KNOWN_TEST_CARD_NUMBERS = {
    "4111111111111111", "4012888888881881", "5555555555554444",
    "5105105105105100", "378282246310005", "371449635398431",
    "6011111111111117", "6011000990139424", "30569309025904",
}


def detect_card_number(text: str) -> list[Hit]:
    hits = []
    for m in CARD_CANDIDATE_RE.finditer(text):
        raw = m.group(0)
        digits = re.sub(r"[ -]", "", raw)
        if len(digits) not in (15, 16):
            continue
        if not _luhn_ok(digits):
            continue
        passage = _passage_around(text, m.start(), m.end())
        is_test_card = digits in KNOWN_TEST_CARD_NUMBERS
        hits.append(Hit("card_number", raw, digits, passage,
                         confidence=0.05 if is_test_card else 0.98,
                         method="deterministic_checksum", suppressed_as_false_positive=is_test_card, char_start=m.start(), char_end=m.end()))
    return hits


MASKED_CARD_RE = re.compile(r"\*{2,4}\s*(\d{4})\b")
ENDING_IN_RE = re.compile(r"ending in[^0-9]{0,10}(\d{4})\b", re.IGNORECASE)


def detect_card_last4(text: str) -> list[Hit]:
    """A masked-card display ("**** 5332") always shows the true last 4
    digits right after the mask — unlike a loose "card ... <4 digits within
    15 chars>" search, which previously grabbed the masked PREFIX digits
    off displays like "9999 **** **** 5332" instead of the actual last 4."""
    hits = []
    for m in MASKED_CARD_RE.finditer(text):
        passage = _passage_around(text, m.start(), m.end())
        hits.append(Hit("card_number", m.group(1), m.group(1), passage, confidence=0.6,
                         method="deterministic_regex", is_partial=True, char_start=m.start(), char_end=m.end()))
    return hits


def detect_ending_in_last4(text: str) -> list[Hit]:
    """"...ending in 1234" is ambiguous between a card and a bank account
    reference — categorize by which keyword appears in the preceding
    clause rather than assuming card, which previously mislabeled every
    "final paycheck issued to account ending in 2991" as a card number."""
    hits = []
    for m in ENDING_IN_RE.finditer(text):
        window = text[max(0, m.start() - 40): m.start()].lower()
        category = "card_number" if "card" in window else "financial_account"
        passage = _passage_around(text, m.start(), m.end())
        hits.append(Hit(category, m.group(1), m.group(1), passage, confidence=0.55,
                         method="deterministic_regex", is_partial=True, char_start=m.start(), char_end=m.end()))
    return hits


def detect_drivers_license(text: str) -> list[Hit]:
    hits = []
    for m in DRIVERS_LICENSE_RE.finditer(text):
        passage = _passage_around(text, m.start(), m.end())
        has_context = "license" in passage.lower() or "dl" in passage.lower()
        hits.append(Hit("drivers_license", m.group(0), m.group(0), passage,
                         confidence=0.9 if has_context else 0.3, method="deterministic_regex", char_start=m.start(), char_end=m.end()))
    return hits


def detect_passport(text: str) -> list[Hit]:
    hits = []
    for m in PASSPORT_CONTEXT_RE.finditer(text):
        passage = _passage_around(text, m.start(), m.end())
        hits.append(Hit("passport", m.group(1), m.group(1), passage, confidence=0.9,
                         method="deterministic_regex", char_start=m.start(), char_end=m.end()))
    return hits


# Regression test: templates like "Patient: {name}    DOB: {dob}" put a
# second field on the SAME line. The first fix attempt required exactly
# one space between name words (multi-space runs being a same-line field
# separator in the raw template) — but OCR collapses whitespace, so a
# scanned copy of that same template renders as "...Perez DOB: ..." with
# a single space, indistinguishable from a real 4th name word by spacing
# alone (confirmed against the actual OCR output, not assumed). The
# robust signal is that "DOB:" is immediately followed by a colon and no
# real name word ever is. A bare negative lookahead (?!:) right after the
# greedy word match isn't enough on its own — the regex engine just
# backtracks the word shorter (matched "DO" instead of "DOB", since "DO"
# isn't immediately followed by a colon either) rather than rejecting the
# word outright, which produced "Christopher Diane Perez DO". Requiring
# the word to end at a space/newline/end-of-string closes that: "DOB",
# "DO", and "D" all fail (followed by ':', 'B', 'O' respectively — none
# of which are the required space/newline/end), so no partial match of
# the label can succeed at any length.
# Label-based detectors are built from the corpus profile (see
# profile.py) rather than hardcoded, because label vocabulary is the ONE
# part of detection that measurably does not generalize: on a document
# using "Beneficiary Full Legal Name ......" instead of "Patient:", the
# format-defined detectors (SSN/DOB/email/phone/card) all still fired and
# every label-defined one missed. Vocabulary is engagement data, not code.
#
# The separator accepts a colon, a run of dots, or an equals sign — form
# layouts use all three, and hardcoding ":" was itself a coupling.
# Separator between a label and its value. Punctuation is OPTIONAL, which
# matters: a first version required it and silently broke two detectors —
# financial_account 0.990 -> 0.307 and login_credentials 1.000 -> 0.000 —
# because this corpus writes "account number 7801948900" with nothing but
# a space. Form layouts use colons or dot leaders; prose uses neither.
# Accept all of them, requiring whitespace when punctuation is absent so
# the label cannot run into the value.
# The comma is what makes an email sign-off reachable: "Best, A. Carter"
# is how a correspondent is named in most real messages, and with only
# colon/dots/equals/whitespace the entire .eml format contributed no
# names at all.
# A single newline is allowed ONLY after an explicit punctuation
# delimiter. "Best,\nA. Carter" and "Patient:\nJane Okafor" both put the
# value on the next line, which is normal in email sign-offs and in form
# layouts. The whitespace-only branch still cannot cross a line: letting
# it do so is what previously made the name regex bleed across a
# paragraph and capture unrelated words.
_SEP = r"(?:[ 	]*(?:[:,]|\.{2,}|=)[ 	]*\n?[ 	]*|[ 	]+)"


@lru_cache(maxsize=1)
def _profile():
    from app.pipeline.detectors.profile import CorpusProfile
    return CorpusProfile.load()


# A captured value must not be a PARTIAL token. A bare "not followed by a
# colon" guard fails: the engine backtracks one character shorter until the
# guard passes, which turned "Login username: jason.yates14" into
# "usernam". Anchoring on whitespace fails the other way — it rejected
# "account number 3070987704." for its trailing period, costing
# financial_account 0.99 -> 0.84. The correct anchor is "not followed by
# more of the value's own character class", which rejects partial tokens
# while still allowing normal sentence punctuation to follow.
_VALUE_END_DIGITS = r"(?!\d)"
# Two guards, both needed. (?![A-Za-z0-9._-]) stops a PARTIAL token: without
# it the engine backtracks one character shorter until any weaker guard
# passes, which turned "Login username: jason.yates14" into "usernam".
# (?![ \t]*:) stops capturing a LABEL as if it were a value: with only the
# first guard, "Login" matched as the label and "username" was captured as
# the value. An earlier attempt anchored on whitespace instead, which broke
# the opposite way — it rejected "account number 3070987704." over its
# trailing period and cost financial_account 0.99 -> 0.84.
_VALUE_END_TOKEN = r"(?![A-Za-z0-9._-])(?![ \t]*:)"


# How a postal address opens: a street number, or one of the standard
# non-numeric prefixes. Anchoring the START is what stops the capture
# beginning in the middle of a sentence.
_ADDRESS_START = (
    r"(?:\d|"
    r"P\.?O\.?\s*Box|PSC|APO|FPO|DPO|USS|USNS|USCGC|"
    r"Unit\b|Suite\b|Ste\.?|Apt\.?|Apartment\b|Flat\b|Floor\b|Level\b|Box\b"
    r")"
)

# Where a captured address ends: a real sentence boundary, the end of
# the line, or a table-cell divider. A period that follows a street
# abbreviation is not a sentence boundary — each exclusion below is a
# fixed-width lookbehind because Python requires that.
_STREET_ABBREVS = ("St", "Ave", "Rd", "Dr", "Ln", "Ct", "Pl", "Cir", "Ter",
                    "Hwy", "Pkwy", "Apt", "Ste", "Fl", "Rm", "No", "Mt", "Ft",
                    "Blvd", "Sq", "Trl", "Way")
# Words that open a sentence but never open an address line. They end
# the capture even after a street abbreviation, which resolves the one
# genuinely ambiguous case: "12 Oak Ave. The patient confirmed ..." is a
# sentence continuing, while "123 Main St. Apt 4" is still the address.
_SENTENCE_OPENERS = (
    r"The|This|That|These|Those|It|Its|A|An|And|He|She|They|We|You|I|"
    r"Please|All|Any|No|Correspondence|Payroll|Notice|Payment|Contact"
)
_ADDRESS_END = (
    rf"\.\s+(?:{_SENTENCE_OPENERS})\b"
    "|"
    + "".join(rf"(?<!\b{a})" for a in _STREET_ABBREVS)
    + r"\.\s+[A-Z]|\.$|\n|\||$"
)

# A label must start at a word boundary. Without this, short entries in
# the vocabulary match inside longer words — "re" inside "Therefore",
# "to" inside "Custodian to" — and the capture that follows is nonsense.
# It mattered little while every label was a distinct noun; it matters
# now that the vocabulary includes short prose cues.
_LABEL_START = r"\b"


@lru_cache(maxsize=1)
def _label_res() -> dict[str, re.Pattern]:
    p = _profile()
    # Each name word must end at a delimiter so a following "DOB:"-style
    # label can never be swallowed as a name word — see the regression
    # test for the OCR-collapsed-whitespace case.
    #
    # The delimiter set includes sentence punctuation, which matters far
    # more than it looks. With only space/newline, "Patient of record:
    # K. Warner, date of birth ..." lost "Warner" to its trailing comma,
    # leaving a single word — and since a name needs at least two, the
    # whole name went undetected. In running prose a name is followed by
    # a comma or a full stop more often than by a bare space, so this
    # single character class was a large part of why prose recall sat at
    # 0.61 while labelled-field recall was near 1.0.
    #
    # ":" is deliberately NOT in the set: that is the guard which stops a
    # following field label being read as part of the name.
    name_word = r"[A-Z][A-Za-z.'-]+(?=[ \n,;)]|$)"
    return {
        # Words within a name may be separated by ", " as well as a
        # space, so the "Surname, Forename" filing order is recognised.
        # It is a normal way for a record system to write a name and it
        # accounted for most of the remaining prose misses. The comma
        # form cannot run away: the following token has to start with a
        # capital, so "Morales, Christopher, date of birth" stops at
        # "date".
        "name": re.compile(
            _LABEL_START + r"(?i:" + p.label_alternation(p.name_labels) + r")" + _SEP
            + r"(" + name_word + r"(?:(?:, | )" + name_word + r"){1,3})"),
        "address": re.compile(
            _LABEL_START + r"(?i:" + p.label_alternation(p.address_labels) + r")" + _SEP
            # Capture to end of line rather than anchoring on a US 5-digit
            # ZIP. Measured: the ZIP anchor silently failed on "88 Kingsway
            # Court, Flat 12, Bristol, BS1 4ND" because a UK postcode is
            # alphanumeric — an address detector that only works in one
            # country is the same class of coupling as a hardcoded label.
            #
            # The value must now BEGIN like an address and STOP at the end
            # of the sentence. "Requiring at least one digit somewhere"
            # was far too weak once documents contained prose: the label
            # matched mid-sentence and the capture ran on through it, so
            # "The address recorded at appointment was 25913 Shepherd
            # Stravenue" was stored as an address value beginning
            # "recorded at appointment was", and one capture swallowed a
            # phone number instead. 254 of 380 address values did not
            # match anything the corpus planted.
            # Stop at a SENTENCE boundary, not at any period, and never
            # at a street abbreviation. Stopping at any period truncated
            # "123 Main St. Apt 4" to "123 Main St", which matches no
            # planted value — address recall fell 0.975 -> 0.631 on
            # abbreviations alone. The exclusion list is street suffixes
            # and unit markers, which are stable across engagements in a
            # way label vocabulary is not.
            + r"(" + _ADDRESS_START + r"[^\n|]{9,119}?)(?=" + _ADDRESS_END + r")",
            re.IGNORECASE),
        "account": re.compile(
            _LABEL_START + r"(?i:" + p.label_alternation(p.account_labels) + r")" + _SEP + r"(\d{6,17})" + _VALUE_END_DIGITS,
            re.IGNORECASE),
        "login": re.compile(
            _LABEL_START + r"(?i:" + p.label_alternation(p.login_labels) + r")" + _SEP + r"([a-zA-Z0-9._-]{4,40})" + _VALUE_END_TOKEN,
            re.IGNORECASE),
    }


# Structured, label-prefixed extraction. This is a deliberate scope choice:
# our templated breach documents put PII behind explicit field labels
# ("Patient:", "Home address:"), which a cheap regex handles reliably. Free
# narrative prose without labels would need the LLM/NER tier — see the
# design doc's pipeline-vs-agent boundary section for the trade-off.


def detect_labeled_name(text: str) -> list[Hit]:
    hits = []
    for m in _label_res()["name"].finditer(text):
        name = m.group(1).strip()
        if name in ("John Doe",):
            continue  # known QA-fixture placeholder
        passage = _passage_around(text, m.start(), m.end())
        hits.append(Hit("full_name", name, name, passage, confidence=0.85, method="deterministic_regex", char_start=m.start(), char_end=m.end()))
    return hits


def detect_labeled_address(text: str) -> list[Hit]:
    hits = []
    for m in _label_res()["address"].finditer(text):
        val = m.group(1).strip().rstrip(".")
        passage = _passage_around(text, m.start(), m.end())
        hits.append(Hit("home_address", val, val, passage, confidence=0.8, method="deterministic_regex", char_start=m.start(), char_end=m.end()))
    return hits


def detect_labeled_account(text: str) -> list[Hit]:
    hits = []
    for m in _label_res()["account"].finditer(text):
        val = m.group(1)
        passage = _passage_around(text, m.start(), m.end())
        hits.append(Hit("financial_account", val, val, passage, confidence=0.75, method="deterministic_regex", char_start=m.start(), char_end=m.end()))
    return hits


def _is_label_word(value: str) -> bool:
    """True when the captured 'value' is really another field caption.

    Two labels in a row defeat the value-end guard: in "Login username:
    jason.yates14" the alternation can match "Login", the separator eats
    the space, and "username" is captured as the credential. 36 of 66
    login values were the literal word "username". Cheap to catch here,
    and it applies to any vocabulary rather than to one phrasing.
    """
    return value.strip().lower() in _profile().all_label_words()


def detect_labeled_login(text: str) -> list[Hit]:
    hits = []
    for m in _label_res()["login"].finditer(text):
        val = m.group(1)
        if _is_label_word(val):
            continue
        passage = _passage_around(text, m.start(), m.end())
        hits.append(Hit("login_credentials", val, val, passage, confidence=0.7, method="deterministic_regex", char_start=m.start(), char_end=m.end()))
    return hits


ALL_DETECTORS = [
    detect_ssn, detect_ssn_last4, detect_email, detect_phone, detect_dob,
    detect_card_number, detect_card_last4, detect_ending_in_last4, detect_drivers_license, detect_passport,
    detect_labeled_name, detect_labeled_address, detect_labeled_account, detect_labeled_login,
]


def run_all(text: str) -> list[Hit]:
    hits: list[Hit] = []
    for detector in ALL_DETECTORS:
        hits.extend(detector(text))
    return hits
