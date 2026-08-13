from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Default to a local SQLite file for zero-friction dev; production target
    # is Postgres (see docker-compose.yml and the design doc's stack
    # justification for the rejected-alternatives reasoning).
    database_url: str = "sqlite:///./breach_analytics.db"

    # Signs the session cookies as well as authenticating header calls.
    #
    # The default is deliberately an obvious placeholder rather than a
    # working secret. On a deployed instance it is replaced at startup by
    # a random value (see main.py) instead of being trusted, because a
    # publicly-known signing key means anyone can forge a session — which
    # is harmless while the demo is open to all, and a real hole the
    # moment someone adds DEMO_PASSWORD and assumes the gate holds.
    api_key: str = "dev-local-key-change-me"

    @property
    def api_key_is_placeholder(self) -> bool:
        return self.api_key == "dev-local-key-change-me"

    anthropic_api_key: str | None = None

    # Cheap tier: pluggable provider, all via OpenAI-compatible endpoints so
    # one code path (see llm_extraction._call_openai_compatible) serves all
    # of them. Set CHEAP_PROVIDER to pick one explicitly; otherwise
    # get_cheap_provider() below auto-picks the first configured one in
    # priority order. Kimi (open-weight Kimi K2) is the primary choice per
    # the design doc's stack justification; Gemini/Groq are free-tier
    # fallbacks when Kimi's account has no balance, and Ollama is a fully
    # local, zero-cost, zero-signup option if you'd rather run nothing
    # over the network at all.
    #
    # NOTE: none of these substitute for the four agents, which are built
    # on claude_agent_sdk (Anthropic-specific) — there is no free path to a
    # live agent run; only ANTHROPIC_API_KEY unlocks that.
    cheap_provider: str | None = None  # "azure" | "kimi" | "gemini" | "groq" | "ollama" | None (auto)

    # Azure AI Foundry: a program-provisioned resource (DataFactZ's shared
    # sponsorship subscription), not a personal free-tier account — live-
    # verified with a real key: deepseek-v3.2 works immediately as the
    # cheap tier, and gpt-5/gpt-5.5 work as a real strong-tier substitute
    # for the extraction escalation path specifically (NOT the four
    # agents, which are hard-wired to claude_agent_sdk/Anthropic and have
    # no substitute). Uses the new unified /openai/v1 endpoint, which is
    # OpenAI-client-compatible like the other providers.
    azure_api_key: str | None = None
    azure_openai_endpoint: str = "https://ai-training-msftfoundry.openai.azure.com/openai/v1"
    azure_cheap_model: str = "deepseek-v3.2"
    azure_strong_model: str = "gpt-5.5"

    kimi_api_key: str | None = None
    kimi_base_url: str = "https://api.moonshot.ai/v1"
    # kimi-k3 (2.8T params, released July 2026) is Moonshot's current
    # flagship, superseding the k2 line this design doc originally
    # targeted — updated to the current model id, though this doesn't
    # change the fact that a Kimi account needs a funded balance
    # regardless of which model on it you call.
    kimi_model: str = "kimi-k3"

    # NOTE: live-tested and found much more quota-constrained in practice
    # than published estimates suggest — see PROVIDER_PRIORITY's comment
    # in llm_extraction.py. gemini-2.0-flash returned 0 free quota on
    # every metric for this project; gemini-3.6-flash allows only 20
    # requests/day. Kept as a fallback, not the recommended default.
    gemini_api_key: str | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    gemini_model: str = "gemini-3.6-flash"

    # Groq hosts Kimi K2 directly (moonshotai/kimi-k2-instruct) with a
    # genuine free tier (1,000 req/day, no card) — this is the recommended
    # real path for the cheap tier: it's the exact model this project's
    # design doc already committed to, just served by a host with a free
    # tier instead of requiring a funded Moonshot account.
    groq_api_key: str | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "moonshotai/kimi-k2-instruct"

    # Ollama needs no key — it's a local server. Only used if explicitly
    # selected (cheap_provider="ollama") or nothing else is configured.
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "llama3.1"

    strong_model: str = "claude-sonnet-5"
    confidence_escalation_threshold: float = 0.72

    # How many times to ask the cheap model the same question, keeping
    # the union of the answers. Implemented, measured, and defaulted OFF.
    #
    # The motivation was real: temperature=0 does not make this model
    # deterministic. Three identical calls on an out-of-distribution
    # document stating "early-onset Parkinson's" returned 0, 0, then 1
    # elements, so a single call looked like a coin flip.
    #
    # It does not generalise. Measured over 55 corpus documents the
    # manifest says contain medical information, samples=3 recovered
    # exactly ZERO values that samples=1 missed — 18/55 either way, at
    # 3.0x the tokens. The corpus misses are consistent, not stochastic:
    # the model reliably declines those, and repeating the question does
    # not change its mind. Reproduce with `python measure_sampling.py`.
    #
    # Kept at 1 because paying triple for a measured gain of zero is not
    # a trade worth making. Kept in the code because the non-determinism
    # is real and a different corpus, model or prompt could surface it.
    llm_samples: int = 1

    # Comma-separated categories the LLM tier may extract. Empty means
    # the measured default (medical only) — see DEFAULT_LLM_CATEGORIES in
    # app/services/llm_extraction.py for the evidence behind that scope.
    llm_categories: str = ""

    corpus_dir: str = "../corpus-generator/output"

    # Path to the tesseract binary if it's not on PATH (e.g. Windows installs
    # to Program Files by default and doesn't always register on PATH).
    tesseract_cmd: str | None = None

    # Agent hygiene: hard budgets (brief section 5)
    max_agent_steps_per_run: int = 500
    max_agent_spend_usd_per_run: float = 25.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
