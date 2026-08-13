"""Corpus profile: the parts of detection that are engagement-specific.

Measured motivation, not a hypothetical. Running the deterministic tier
against a document written in a different house style — "Beneficiary
Full Legal Name ...... Maria Elena Vasquez" instead of "Patient: ..." —
produced this split:

    format-defined detectors   SSN, DOB, email, phone, card  -> all FOUND
    label-defined detectors    full_name, home_address        -> all MISSED

That is the whole generalization story. A pattern defined by its *shape*
(nine digits grouped 3-2-4, a Luhn-valid 16-digit run, an RFC-shaped
address) transfers to any corpus for free. A pattern defined by the
*words around it* only works on documents that use those exact words, and
the previous implementation had that vocabulary hardcoded in Python —
which silently made the whole system a single-corpus tool.

So label vocabulary lives here as data, loaded from a JSON profile, and
ships with a deliberately broad default rather than the phrasing of one
synthetic corpus. A new engagement points CORPUS_PROFILE at its own file
and changes no code. `discover_labels()` bootstraps that file by mining
the corpus for recurring "Label: value" shapes, so an operator starts
from what the documents actually say instead of guessing.

The universal detectors deliberately do NOT read this file. They must
keep working on a corpus nobody has profiled yet.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field

# Broad defaults: common business-document phrasing across HR, medical,
# insurance, legal and financial contexts — not the label set of any one
# corpus. Extend per engagement via a profile file rather than by editing
# this list.
DEFAULT_NAME_LABELS = [
    "patient", "employee", "claimant", "customer", "contact", "to", "bill to",
    "name", "full name", "legal name", "full legal name", "beneficiary",
    "beneficiary name", "beneficiary full legal name", "account holder",
    "policy holder", "policyholder", "insured", "member", "member name",
    "client", "applicant", "recipient", "subject", "individual", "person",
    "cardholder", "borrower", "tenant", "resident", "guardian", "next of kin",

    # Prose cues. A colon-delimited field is only one of the ways a
    # document names its subject; running prose says "in the matter of
    # Jane Okafor" or "this bundle relates to ...". Measured on the
    # multi-page corpus: 42 of 47 unnamed identities came from documents
    # where the name was present in a sentence and every label-adjacent
    # pattern missed it, leaving a quarter of the exposure table with no
    # name to notify.
    #
    # These sit here rather than in the regex for the same reason the
    # rest of the vocabulary does — they are the phrasing of a house
    # style, and a different engagement will use different ones.
    "patient of record", "in the matter of", "in re", "relates to",
    "on behalf of", "prepared for", "issued to", "statement for",
    "record for", "file for", "regarding", "concerning", "with respect to",

    # Email sign-offs. A correspondent's name is very often given only in
    # the closing, never in a labelled field, so without these an entire
    # format contributes no names at all.
    "best", "best regards", "kind regards", "regards", "sincerely",
    "many thanks", "thanks", "yours", "yours sincerely", "yours faithfully",
]

DEFAULT_ADDRESS_LABELS = [
    "home address", "mailing address", "shipping address", "address",
    "residential address", "street address", "postal address", "billing address",
    "notices shall be sent to", "residence", "domicile", "location",
    "address on file", "current address", "permanent address",
]

DEFAULT_ACCOUNT_LABELS = [
    "account", "account number", "bank account", "acct", "acct no",
    "account no", "iban", "sort code", "routing", "routing number",
    "payment instrument", "primary payment instrument", "deposit account",
]

DEFAULT_LOGIN_LABELS = [
    "username", "user name", "login", "logon", "user id", "userid",
    "screen name", "handle", "account name",

    # Prose cues. Every one of these labels is a form caption, and a
    # personnel file does not use form captions — it says "a network
    # account was provisioned as ryan.walter41". Measured: 0 of 74
    # credentials in text-layer documents were detected, because the
    # credential is always introduced by a verb rather than a label.
    "provisioned as", "created as", "issued as", "registered as",
    "set up as", "logs in as", "signs in as", "authenticates as",
    "network account", "system account", "domain account", "account id",
]

# Mailbox local-parts that denote a shared/departmental address rather
# than a person. Broad by default; an engagement adds its own.
DEFAULT_SHARED_MAILBOXES = [
    "support", "info", "sales", "help", "helpdesk", "admin", "administrator",
    "contact", "enquiries", "inquiries", "noreply", "no-reply", "donotreply",
    "postmaster", "webmaster", "hr", "payroll", "billing", "accounts",
    "legal", "compliance", "privacy", "security", "abuse", "records",
    "team", "office", "mail", "general",
]


@dataclass
class CorpusProfile:
    name_labels: list[str] = field(default_factory=lambda: list(DEFAULT_NAME_LABELS))
    address_labels: list[str] = field(default_factory=lambda: list(DEFAULT_ADDRESS_LABELS))
    account_labels: list[str] = field(default_factory=lambda: list(DEFAULT_ACCOUNT_LABELS))
    login_labels: list[str] = field(default_factory=lambda: list(DEFAULT_LOGIN_LABELS))
    shared_mailboxes: list[str] = field(default_factory=lambda: list(DEFAULT_SHARED_MAILBOXES))

    @classmethod
    def load(cls, path: str | None = None) -> "CorpusProfile":
        """Loads a profile file, falling back to the broad defaults.

        A profile may override any subset of keys; anything it omits keeps
        the default, so a one-line file adding a single unusual label is
        valid and doesn't silently disable everything else.
        """
        path = path or os.environ.get("CORPUS_PROFILE")
        if not path or not os.path.isfile(path):
            return cls()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        base = cls()
        for key in ("name_labels", "address_labels", "account_labels",
                    "login_labels", "shared_mailboxes"):
            if key in data:
                merged = list(dict.fromkeys([*getattr(base, key), *data[key]]))
                setattr(base, key, merged)
        return base

    def all_label_words(self) -> frozenset[str]:
        """Every caption in the profile, for detecting a value that is
        really just the next field's label. Two labels in a row defeat
        the value-end guard, so "Login username: x" could capture
        "username" as the credential."""
        return frozenset(
            l.strip().lower()
            for group in (self.name_labels, self.address_labels,
                           self.account_labels, self.login_labels)
            for l in group
        )

    def label_alternation(self, labels: list[str]) -> str:
        """Regex alternation, longest-first so "full legal name" wins over
        "name" — otherwise the shorter label matches first and truncates
        the captured value."""
        return "|".join(re.escape(l) for l in sorted(labels, key=len, reverse=True))


# Matches "<words> <separator> <value>" where the separator is a colon, a
# run of dots (common in form-style layouts), or an equals sign. Used only
# for discovery, never for extraction.
_DISCOVERY_RE = re.compile(
    r"^[ \t]*([A-Za-z][A-Za-z /'&-]{2,40}?)[ \t]*(?::|\.{2,}|=)[ \t]*(\S.*)$",
    re.MULTILINE,
)


def discover_labels(texts: list[str], min_occurrences: int = 3, top_n: int = 60) -> list[tuple[str, int, str]]:
    """Mines a corpus for recurring "Label: value" shapes.

    Returns (label, occurrences, example_value) so an operator can build a
    profile from what the documents actually say. This is a bootstrapping
    aid for a new engagement — it does not classify labels into
    categories, because deciding whether "Beneficiary" means a person's
    name or an organisation is exactly the judgment a human (or the LLM
    tier) should make, not a frequency count.
    """
    counts: Counter[str] = Counter()
    examples: dict[str, str] = {}
    for text in texts:
        for m in _DISCOVERY_RE.finditer(text):
            label = " ".join(m.group(1).split()).lower()
            value = m.group(2).strip()
            if not value or len(label) < 3:
                continue
            counts[label] += 1
            examples.setdefault(label, value[:60])
    return [(l, c, examples[l]) for l, c in counts.most_common(top_n) if c >= min_occurrences]
