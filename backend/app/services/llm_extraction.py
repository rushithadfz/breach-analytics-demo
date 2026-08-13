"""LLM extraction tier: context-dependent PII categories that the
deterministic label-based regex tier misses (free narrative text with no
field label, ambiguous phrasing). Cost-tiered per brief section 6:

  1. Kimi (open-weight, cheap) attempts every document that still has gaps
     after the deterministic pass.
  2. Anything Kimi reports below `confidence_escalation_threshold` escalates
     to Claude (strong tier).

Every call is logged as a Step (+ token/cost accounting) so run traces are
inspectable in the UI per the agent-hygiene requirement, even though this
tier is pipeline code, not one of the four named agents.

NOTE ON PRICING: the USD-per-token constants below are placeholders. Per
the program's AI Usage Policy, they must be verified against the current
Anthropic and Moonshot pricing pages before appearing as fact in the design
doc's cost section — do not cite PRICING_USD_PER_1M below as authoritative.
"""
from __future__ import annotations

import json
import random
import re
import time

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    Document, DocumentStatus, Extraction, ExtractionMethod, PiiCategory,
    Run, RunStatus, RunType, Step,
)

# PLACEHOLDER — verify before citing in the design doc, EXCEPT the two
# entries marked "real free tier", which are $0 while usage stays under
# the provider's published free-tier request/token quota — not a
# placeholder, an actual price, until that quota is exceeded.
PRICING_USD_PER_1M = {
    "kimi-k3": {"input": 0.60, "output": 2.50},  # Moonshot direct API — needs a funded account regardless of price
    "claude-sonnet-5": {"input": 3.00, "output": 15.00},
    "gemini-3.6-flash": {"input": 0.0, "output": 0.0},  # real free tier — recommended default
    # Groq-hosted Kimi K2 (moonshotai/kimi-k2-instruct): $0 under Groq's
    # free tier (1,000 req/day, no card); $1.00/$3.00 per 1M in/out if
    # that quota is ever exceeded and billing is enabled on the account.
    "moonshotai/kimi-k2-instruct": {"input": 0.0, "output": 0.0},  # real free tier
    "llama-3.3-70b-versatile": {"input": 0.0, "output": 0.0},  # real free tier (Groq)
    "llama3.1": {"input": 0.0, "output": 0.0},  # local Ollama — literally free, always
    # Azure AI Foundry (program-provisioned sponsorship subscription, not
    # a personal pay-per-token account) — treated as $0 for this project's
    # purposes since usage draws on the program's allocation, not personal
    # billing. Live-verified to work; per-token list price not verified.
    "deepseek-v3.2": {"input": 0.0, "output": 0.0},
    "gpt-5.5": {"input": 0.0, "output": 0.0},
}

# Real, measured free-tier rate limits (requests per minute), not
# estimates — gemini-3.6-flash's 5 RPM came directly from a live 429
# response (GenerateRequestsPerMinutePerProjectPerModel-FreeTier), not a
# blog post. Pacing calls to stay under these avoids most 429s outright;
# _call_with_retry below handles the ones pacing alone can't prevent
# (concurrent usage on the same key, clock drift, etc.).
RATE_LIMIT_RPM = {
    "gemini-3.6-flash": 5,
    "moonshotai/kimi-k2-instruct": 30,  # Groq free tier
    "llama-3.3-70b-versatile": 30,      # Groq free tier
    "kimi-k3": 60,                       # assumed generous on a funded account; irrelevant while unfunded
    "llama3.1": 0,                       # local Ollama — no rate limit
}


def _call_with_retry(fn, *args, max_retries: int = 3, **kwargs):
    """Retries once per 429, sleeping for the delay the API itself
    reports (RetryInfo) rather than a guessed backoff — the free tier
    tells you exactly how long to wait."""
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            message = str(e)
            is_rate_limit = "429" in message or "RESOURCE_EXHAUSTED" in message
            if not is_rate_limit or attempt == max_retries:
                raise
            match = re.search(r"retry(?:Delay)?['\"]?:?\s*['\"]?(\d+(?:\.\d+)?)s?", message, re.IGNORECASE)
            delay = float(match.group(1)) if match else 15.0
            time.sleep(min(delay, 60.0) + 1.0)  # +1s safety margin


MOCK_SEED = 20260101  # fixed seed: mock runs are reproducible, not just random noise

# Which categories the LLM tier is allowed to extract.
#
# Scoped to medical on measured evidence (design doc §8.0). Running the
# tier over all five categories was compared against the deterministic
# tier alone on the same corpus:
#
#   category            recall det -> llm    value precision det -> llm
#   medical                 0.000 -> 0.738            n/a -> 0.600
#   login_credentials       0.704 -> 1.000          0.986 -> 0.764
#   financial_account       0.920 -> 1.000          1.000 -> 0.755
#   home_address            0.981 -> 1.000          0.960 -> 0.949
#
# Only medical is a category the deterministic tier cannot do at all.
# Everywhere else the LLM buys recall by giving up roughly a quarter of
# its value precision, and for a breach notification a fabricated
# account number on someone's record is a worse outcome than an unfound
# one — you cannot un-tell somebody their bank details leaked.
#
# Widening this is a legitimate engagement decision (a client who cares
# more about coverage than about precision may want the full set), so it
# is a setting rather than an edit: LLM_CATEGORIES=medical,home_address
DEFAULT_LLM_CATEGORIES = [PiiCategory.medical]

ALLOWED_LLM_CATEGORIES = [
    PiiCategory.full_name, PiiCategory.home_address, PiiCategory.medical,
    PiiCategory.login_credentials, PiiCategory.financial_account,
]


def llm_categories() -> list[PiiCategory]:
    """Configured scope, falling back to the measured default."""
    configured = (get_settings().llm_categories or "").strip()
    if not configured:
        return list(DEFAULT_LLM_CATEGORIES)

    out = []
    for name in (n.strip() for n in configured.split(",")):
        if not name:
            continue
        try:
            cat = PiiCategory(name)
        except ValueError:
            raise ValueError(
                f"LLM_CATEGORIES contains unknown category {name!r}. "
                f"Valid: {', '.join(c.value for c in ALLOWED_LLM_CATEGORIES)}"
            ) from None
        if cat not in ALLOWED_LLM_CATEGORIES:
            raise ValueError(
                f"LLM_CATEGORIES contains {name!r}, which the deterministic "
                f"tier owns. Valid: {', '.join(c.value for c in ALLOWED_LLM_CATEGORIES)}"
            )
        out.append(cat)
    return out


class ExtractedElement(BaseModel):
    category: str = Field(description="one of: full_name, home_address, medical, login_credentials, financial_account")
    value: str
    passage: str = Field(description="verbatim source sentence/line the value came from")
    confidence: float = Field(ge=0.0, le=1.0)


class LlmExtractionResult(BaseModel):
    elements: list[ExtractedElement]


_EXTRACTION_PROMPT_TEMPLATE = """You extract personal data elements from a document for a breach-notification exposure analysis.
Only extract: {categories}.
Do NOT extract anything else — every other category is handled by deterministic detectors that outperform you on it.
Do NOT invent values. Every "value" MUST be a verbatim substring of the "passage" you cite for it — if you cannot point
to the exact words in the document, do not emit the element at all. This is checked programmatically; fabricated values are discarded.
"medical" means clinical/health information about the person specifically (a diagnosis, a treatment, a condition) —
NOT an incident report saying someone was uninjured (e.g. "no injuries" in a car-accident claim is not medical information).
Extract the COMPLETE clinical phrase, not a shortened form: if the document says "Seasonal allergies, no medication",
the value is "Seasonal allergies, no medication" — not just "Seasonal allergies". Capture the full phrase up to the
end of the line or sentence.
If a value looks like a template placeholder, a test fixture (e.g. "John Doe"), or an internal order/tracking reference, omit it and explain nothing — just don't emit it.

Return JSON with EXACTLY this shape — the field names below are literal, not descriptions, and no other field names are acceptable:
{{"elements": [{{"category": "{example_category}", "value": "a verbatim value", "passage": "the exact source sentence the value came from", "confidence": 0.9}}]}}

"category" must be one of: {categories}.
"confidence" reflects how certain you are this is a real personal data element belonging to a named individual, not a false positive.
If nothing qualifies, return {{"elements": []}}."""


def extraction_system_prompt(categories: list[PiiCategory] | None = None) -> str:
    """Builds the prompt for the configured scope.

    The category list was previously hardcoded in the prompt AND in the
    filter that drops out-of-scope elements. With the scope now
    configurable those two could disagree, and the model would be asked
    for categories whose output is silently discarded — wasted tokens and
    a confusing trace. One source of truth instead.
    """
    cats = categories or llm_categories()
    names = ", ".join(c.value for c in cats)
    return _EXTRACTION_PROMPT_TEMPLATE.format(
        categories=names, example_category=cats[0].value if cats else "medical"
    )


def _cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    pricing = PRICING_USD_PER_1M.get(model, {"input": 0, "output": 0})
    return (tokens_in / 1_000_000) * pricing["input"] + (tokens_out / 1_000_000) * pricing["output"]


# Priority order for auto-selecting the cheap-tier provider when
# CHEAP_PROVIDER isn't set explicitly.
#
# Azure is first: a program-provisioned resource (DataFactZ's shared
# sponsorship subscription, not a personal free-tier account), live-
# verified with deepseek-v3.2 working immediately with no quota surprises
# of the kind Gemini's personal free tier had. Groq is next (published
# 1,000 req/day, not yet independently verified the same rigorous way).
# Gemini is third — live-tested and found far more quota-constrained than
# advertised: 0 requests/day for gemini-2.0-flash (every metric returned
# limit: 0), and only 20/day for gemini-3.6-flash (confirmed via a live
# 429, not a blog post). Kimi's own direct API is fourth despite being
# the design doc's original "primary" choice, because a configured
# KIMI_API_KEY on this project has a known zero balance. Local Ollama is
# last since it needs the most setup (install + pull a model).
PROVIDER_PRIORITY = ["azure", "groq", "gemini", "kimi", "ollama"]


def _resolve_cheap_provider(settings) -> tuple[str, str, str, str | None]:
    """Returns (provider_name, base_url, model, api_key). Honors an
    explicit settings.cheap_provider choice; otherwise picks the first
    configured provider in PROVIDER_PRIORITY. Ollama needs no key, so it's
    only auto-picked if nothing else is configured — an unconfigured
    Ollama server would otherwise fail every call with a connection error
    instead of a clear "set an API key" message."""
    candidates = {
        "azure": (settings.azure_openai_endpoint, settings.azure_cheap_model, settings.azure_api_key),
        "kimi": (settings.kimi_base_url, settings.kimi_model, settings.kimi_api_key),
        "groq": (settings.groq_base_url, settings.groq_model, settings.groq_api_key),
        "gemini": (settings.gemini_base_url, settings.gemini_model, settings.gemini_api_key),
        "ollama": (settings.ollama_base_url, settings.ollama_model, "ollama"),  # openai client requires a non-empty key string
    }

    if settings.cheap_provider:
        base_url, model, api_key = candidates[settings.cheap_provider]
        return settings.cheap_provider, base_url, model, api_key

    for name in PROVIDER_PRIORITY:
        base_url, model, api_key = candidates[name]
        if name == "ollama":
            continue  # only used if explicitly selected, or as the final fallback below
        if api_key:
            return name, base_url, model, api_key

    base_url, model, api_key = candidates["ollama"]
    return "ollama", base_url, model, api_key


def _resolve_strong_provider(settings) -> tuple[str, str, str, str | None] | None:
    """Same idea as _resolve_cheap_provider, for the extraction tier's
    escalation step specifically (NOT the four agents, which are
    hard-wired to claude_agent_sdk/Anthropic and have no substitute).
    Prefers Azure's gpt-5.5 when available — a real, program-provisioned
    strong model — falling back to Claude if only ANTHROPIC_API_KEY is
    set. Returns None if neither is configured (escalation skipped)."""
    if settings.azure_api_key:
        return "azure", settings.azure_openai_endpoint, settings.azure_strong_model, settings.azure_api_key
    if settings.anthropic_api_key:
        return "claude", "", settings.strong_model, settings.anthropic_api_key
    return None


_CATEGORY_KEY_ALIASES = ("category", "type", "field", "label")
_PASSAGE_KEY_ALIASES = ("passage", "text", "snippet", "context", "source")
_VALUE_KEY_ALIASES = ("value", "text_value", "extracted_value")


# Field captions a model tends to emit as though they were data.
_LABEL_WORDS = (
    r"username|user name|user id|userid|login|logon|password|passcode|"
    r"account(?:\s+number|\s+no\.?)?|acct|routing(?:\s+number)?|iban|sort code|"
    r"full name|name|address|home address|mailing address|billing address|"
    r"phone(?:\s+number)?|telephone|mobile|email(?:\s+address)?|"
    r"ssn|social security(?:\s+number)?|dob|date of birth|"
    r"card(?:\s+number)?|credentials|value|field"
)
# "account 5877639950" -> "5877639950"
_LEADING_LABEL_RE = re.compile(rf"^\s*(?:{_LABEL_WORDS})\s*[:#-]?\s+", re.IGNORECASE)
# "username" on its own is a caption, not a credential.
_BARE_LABEL_RE = re.compile(rf"(?:{_LABEL_WORDS})", re.IGNORECASE)


def _normalize_element(raw: dict, source_text: str | None = None) -> dict | None:
    """response_format={"type": "json_object"} (unlike a strict json_schema
    mode) only guarantees syntactically valid JSON, not the exact field
    names asked for — a real gap this hit on the first live Gemini call,
    which used "type" instead of "category" and omitted "passage"
    entirely. This tolerates the common variants instead of hard-crashing
    the whole extraction tier on one provider's naming quirk.

    Also strips trailing punctuation from "value" (models inconsistently
    include the sentence's closing period — this was silently deflating
    the accuracy scorer's exact-value matching, not an extraction miss)
    and rejects the element entirely if "value" doesn't actually appear
    in "passage" — a real, measured finding on this corpus: two medical
    values ("Psychologist, counselling", "learning disability") were
    fabricated outright, appearing in no source document at all. A
    genuine extraction should always be able to point at its own quote."""
    out = dict(raw)
    for canonical, aliases in (("category", _CATEGORY_KEY_ALIASES),
                                ("value", _VALUE_KEY_ALIASES),
                                ("passage", _PASSAGE_KEY_ALIASES)):
        if canonical not in out:
            for alias in aliases:
                if alias in out:
                    out[canonical] = out[alias]
                    break
    out.setdefault("passage", out.get("value", ""))
    out.setdefault("confidence", 0.5)

    value = str(out.get("value", "")).strip().rstrip(".,;:")

    # Strip a field label the model carried into the value, and reject
    # the element if the label is ALL there is.
    #
    # Measured on the multi-page corpus: 88 login_credentials elements
    # had the value "username" — the caption, not the credential — and
    # 231 financial_account values were of the form "account 5877639950",
    # the number with its own label glued on. Both look like successful
    # extractions to a category-level scorer and both are wrong: one is
    # not a value at all, the other never matches the real value so it
    # can never join an identity.
    # Checked before AND after stripping. Before, because a multi-word
    # caption like "account number" would otherwise have its first word
    # removed and survive as "number"; after, because stripping can
    # leave a second caption behind.
    if _BARE_LABEL_RE.fullmatch(value):
        return None
    value = _LEADING_LABEL_RE.sub("", value).strip(" :\t-")
    if not value or _BARE_LABEL_RE.fullmatch(value):
        return None

    out["value"] = value

    # Validate against the ACTUAL document text when available, not just
    # the model's self-reported passage. Measured reason: checking only
    # against the passage catches self-INCONSISTENT fabrication, but a
    # model that invents both the value and a matching passage sails
    # through — exactly what happened with "learning disability", which
    # appears nowhere in the corpus (verified by grepping the generator
    # source) yet survived the passage-only check. The document text is
    # ground truth; the model's passage is just another model output.
    haystack = source_text if source_text is not None else str(out.get("passage", ""))
    if value.lower() not in haystack.lower():
        return None
    return out


def _call_openai_compatible(client, model: str, text: str) -> tuple[LlmExtractionResult, int, int]:
    """Shared call path for Kimi, Groq, Gemini, and Ollama — all expose an
    OpenAI-compatible chat completions endpoint, so one function serves
    all four rather than duplicating near-identical request code per
    provider."""
    resp = _call_with_retry(
        client.chat.completions.create,
        model=model,
        messages=[
            {"role": "system", "content": extraction_system_prompt()},
            {"role": "user", "content": f"Document text:\n\n{text[:8000]}"},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    data = json.loads(resp.choices[0].message.content)
    elements = [e for e in (_normalize_element(el, text) for el in data.get("elements", [])) if e is not None]
    usage = resp.usage
    tokens_in = usage.prompt_tokens if usage else len(text) // 4
    tokens_out = usage.completion_tokens if usage else 0
    return LlmExtractionResult.model_validate({"elements": elements}), tokens_in, tokens_out


def _call_gpt5_azure(client, model: str, text: str) -> tuple[LlmExtractionResult, int, int]:
    """GPT-5-series models on Azure need two real, verified deviations
    from the standard OpenAI-compatible shape: max_completion_tokens
    instead of max_tokens, and no explicit temperature at all (0 is
    rejected outright — "Only the default (1) value is supported",
    confirmed via a live 400, not documentation)."""
    resp = _call_with_retry(
        client.chat.completions.create,
        model=model,
        messages=[
            {"role": "system", "content": extraction_system_prompt()},
            {"role": "user", "content": f"Document text:\n\n{text[:12000]}"},
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=2048,
    )
    data = json.loads(resp.choices[0].message.content)
    elements = [e for e in (_normalize_element(el, text) for el in data.get("elements", [])) if e is not None]
    usage = resp.usage
    tokens_in = usage.prompt_tokens if usage else len(text) // 4
    tokens_out = usage.completion_tokens if usage else 0
    return LlmExtractionResult.model_validate({"elements": elements}), tokens_in, tokens_out


def _call_claude(client, text: str) -> tuple[LlmExtractionResult, int, int]:
    settings = get_settings()
    tool = {
        "name": "record_extraction",
        "description": "Record extracted PII elements",
        "input_schema": LlmExtractionResult.model_json_schema(),
    }
    resp = client.messages.create(
        model=settings.strong_model,
        max_tokens=2048,
        system=extraction_system_prompt(),
        tools=[tool],
        tool_choice={"type": "tool", "name": "record_extraction"},
        messages=[{"role": "user", "content": f"Document text:\n\n{text[:12000]}"}],
    )
    tool_use = next(b for b in resp.content if b.type == "tool_use")
    return LlmExtractionResult.model_validate(tool_use.input), resp.usage.input_tokens, resp.usage.output_tokens


def run_llm_extraction_tier(db: Session, corpus_dir: str, mock: bool = False, limit: int | None = None) -> Run:
    """Runs the cheap-tier extraction pass (Kimi/Groq/Gemini/Ollama, all via
    one OpenAI-compatible code path) with optional escalation to Claude.

    Escalation is OPTIONAL, not required: if no ANTHROPIC_API_KEY is set,
    low-confidence elements are kept as-is (flagged for human review
    downstream via their low confidence) rather than blocking the whole
    tier on a key this project may not have yet. That means a fully real,
    live, $0 run is possible with just a free-tier cheap-tier key and no
    Anthropic account at all — the four agents still need Claude
    specifically (claude_agent_sdk has no other-provider equivalent), but
    the extraction tier does not.

    `limit` caps how many candidate documents are processed — useful
    given real free-tier RPM limits: gemini-3.6-flash's measured 5 RPM
    means the full ~755-document corpus takes ~2.5 hours end to end.
    Pass a limit for a genuinely complete (not rate-limit-truncated) run
    over a smaller real sample instead of an interrupted run over
    everything.
    """
    import os

    settings = get_settings()
    cheap_client = claude_client = strong_azure_client = None
    strong_provider_name = None
    can_escalate = False

    # Resolved once per run so the prompt, the "already covered" skip and
    # the out-of-scope filter cannot disagree with each other.
    scope = llm_categories()
    print(f"[llm tier] scope: {', '.join(c.value for c in scope)}")

    if mock:
        from app.services.mock_llm import mock_escalate, mock_extract
        mock_rng = random.Random(MOCK_SEED)
        provider_name = "mock"
        cheap_model_name = "kimi-k3 (mock)"
        strong_model_name = f"{settings.strong_model} (mock)"
    else:
        from openai import OpenAI
        provider_name, base_url, cheap_model_name, api_key = _resolve_cheap_provider(settings)
        if not api_key and provider_name != "ollama":
            raise RuntimeError(
                f"No cheap-tier provider configured. Set one of AZURE_API_KEY, "
                f"KIMI_API_KEY, GROQ_API_KEY, GEMINI_API_KEY (see .env.example), "
                f"or run an Ollama server and set CHEAP_PROVIDER=ollama. Or run "
                f"with mock=True to test the pipeline plumbing without any key. "
                f"Skipped — deterministic-only results stand."
            )
        cheap_client = OpenAI(api_key=api_key, base_url=base_url)

        strong_resolved = _resolve_strong_provider(settings)
        if strong_resolved:
            strong_provider_name, strong_base_url, strong_model_name, strong_api_key = strong_resolved
            can_escalate = True
            if strong_provider_name == "azure":
                strong_azure_client = OpenAI(api_key=strong_api_key, base_url=strong_base_url)
            else:
                from anthropic import Anthropic
                claude_client = Anthropic(api_key=strong_api_key)
        else:
            strong_model_name = settings.strong_model

    run = Run(run_type=RunType.extraction,
              config_json={"tier": "llm", "mock": mock, "cheap_provider": provider_name,
                           "cheap_model": cheap_model_name, "strong_model": strong_model_name,
                           "escalation_available": can_escalate})
    db.add(run)
    db.flush()

    docs_stmt = select(Document).where(Document.status == DocumentStatus.parsed)
    if limit:
        docs_stmt = docs_stmt.limit(limit)
    docs = db.execute(docs_stmt).scalars().all()

    from app.pipeline.parsers.extract_text import extract_parsed_records

    # Pace calls to stay under the resolved model's real free-tier RPM
    # (see RATE_LIMIT_RPM) rather than firing as fast as possible and
    # relying entirely on retry-after-429 — pacing avoids most 429s
    # outright, which matters a lot at 5 RPM on gemini-3.6-flash.
    min_interval = 60.0 / RATE_LIMIT_RPM[cheap_model_name] if (not mock and RATE_LIMIT_RPM.get(cheap_model_name)) else 0.0
    last_call_at = 0.0

    escalations, escalation_skipped = 0, 0
    for doc in docs:
        existing_categories = {e.category for e in doc.extractions if e.category in scope}
        if existing_categories == set(scope):
            continue  # deterministic tier already covered everything we'd look for

        # Parse page-aware and join, rather than calling extract_text: the
        # LLM sees the same string either way, but this keeps the page map
        # so LLM-found values get the same deep-link as regex ones.
        records = extract_parsed_records(doc.sniffed_type, os.path.join(corpus_dir, doc.relpath))
        text = "\n".join(r.text for r in records)
        page_record = records[0] if len(records) == 1 else None
        step_label = f"llm_extraction_{provider_name}" + ("_mock" if mock else "")

        if min_interval:
            wait = min_interval - (time.time() - last_call_at)
            if wait > 0:
                time.sleep(wait)

        t0 = time.time()
        last_call_at = t0
        try:
            if mock:
                result, tin, tout = mock_extract(text, mock_rng)
            else:
                result, tin, tout = _call_openai_compatible(cheap_client, cheap_model_name, text)
            model_used = cheap_model_name
        except Exception as e:
            step = Step(run_id=run.id, agent_name="pipeline", step_type=step_label,
                        input_summary=doc.relpath, output_summary=f"error: {e}", status="error")
            db.add(step)
            continue
        latency_ms = int((time.time() - t0) * 1000)
        cost = 0.0 if mock else _cost_usd(model_used, tin, tout)
        db.add(Step(run_id=run.id, agent_name="pipeline", step_type=step_label,
                     input_summary=doc.relpath, output_summary=f"{len(result.elements)} elements" + (" (mock)" if mock else ""),
                     cost_usd=cost, tokens_in=tin, tokens_out=tout, latency_ms=latency_ms))
        run.total_cost_usd += cost
        run.total_tokens_in += tin
        run.total_tokens_out += tout

        low_confidence = [el for el in result.elements if el.confidence < settings.confidence_escalation_threshold]
        if low_confidence and (mock or can_escalate):
            escalations += 1
            escalation_label = f"llm_extraction_{strong_provider_name}_escalation" if strong_provider_name else "llm_extraction_escalation"
            escalation_label += "_mock" if mock else ""
            t0 = time.time()
            if mock:
                result, tin, tout = mock_escalate(text, low_confidence, mock_rng)
            elif strong_provider_name == "azure":
                result, tin, tout = _call_gpt5_azure(strong_azure_client, strong_model_name, text)
            else:
                result, tin, tout = _call_claude(claude_client, text)
            model_used = strong_model_name
            latency_ms = int((time.time() - t0) * 1000)
            cost = 0.0 if mock else _cost_usd(model_used, tin, tout)
            db.add(Step(run_id=run.id, agent_name="pipeline", step_type=escalation_label,
                         input_summary=doc.relpath, output_summary=f"{len(result.elements)} elements" + (" (mock)" if mock else ""),
                         cost_usd=cost, tokens_in=tin, tokens_out=tout, latency_ms=latency_ms))
            run.total_cost_usd += cost
            run.total_tokens_in += tin
            run.total_tokens_out += tout
        elif low_confidence:
            escalation_skipped += 1

        for el in result.elements:
            if el.category not in {c.value for c in scope}:
                continue
            # The model reports a value, not a character offset, so the
            # offset is recovered by locating the value in the source
            # text. _normalize_element already guarantees the value
            # appears there verbatim. Where a value occurs more than once
            # this cites the first occurrence — the right page for the
            # common case, and an honest approximation otherwise, which
            # is why the UI labels these links "approx." for LLM hits.
            offset = text.find(el.value) if el.value else -1
            db.add(Extraction(
                document_id=doc.id, category=PiiCategory(el.category), raw_value=el.value,
                normalized_value=el.value, passage=el.passage, confidence=el.confidence,
                page_number=page_record.page_for_offset(offset) if page_record else None,
                char_start=offset if offset >= 0 else None,
                char_end=offset + len(el.value) if offset >= 0 else None,
                method=ExtractionMethod.llm_strong if model_used == strong_model_name else ExtractionMethod.llm_cheap,
                model_used=model_used, run_id=run.id,
            ))

    run.total_documents = len(docs)
    run.finish()
    db.commit()
    skip_note = f", {escalation_skipped} low-confidence elements NOT escalated (no ANTHROPIC_API_KEY)" if escalation_skipped else ""
    print(f"LLM extraction run {run.id}{' [MOCK]' if mock else f' [{provider_name}]'}: {len(docs)} docs considered, "
          f"{escalations} escalated{skip_note}, cost ${run.total_cost_usd:.4f}"
          + (" (mock: $0, no real API calls made)" if mock else ""))
    return run
