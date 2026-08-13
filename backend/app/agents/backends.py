"""Backend selection shared by all four agents.

Context, because this corrects an earlier design assumption in this
project: the agents were originally written against claude_agent_sdk
only, and the docs claimed they therefore *required* an Anthropic key
with "no substitute." That turned out to be imprecise. Each of the four
agents asks its model for exactly ONE structured decision per unit of
work — the prompts literally say "call <tool> exactly once" — so none of
them needs a multi-turn agentic tool loop. Any model that can reliably
emit structured JSON serves the same function, which was verified live
against gpt-5.5 on Azure AI Foundry.

Trade-off, stated rather than hidden: the Azure path loses the MCP tool
surface and the SDK's built-in budget/session handling (both of which
map to the CCA-F Tool Design & MCP domain), so claude_agent_sdk remains
the preferred backend whenever an Anthropic key is available. Azure is
the fallback that makes live agent runs possible without one.
"""
from __future__ import annotations

import json

# Appended to each agent's system prompt on the Azure path. The Claude
# path gets its output shape enforced by the SDK's tool schema; plain
# JSON mode guarantees valid JSON but NOT the requested field names — a
# real failure mode measured in this project, where a model returned
# "type" where "category" was asked for. So the shape is spelled out
# literally, with an example, rather than described in prose.
def json_shape_suffix(example: dict, notes: str = "") -> str:
    return (
        "\n\nReturn JSON with EXACTLY these fields and no others:\n"
        + json.dumps(example)
        + ("\n" + notes if notes else "")
    )


def resolve_agent_backend(settings, mock: bool) -> str | None:
    """Returns "mock" | "claude" | "azure" | None (nothing configured)."""
    if mock:
        return "mock"
    if settings.anthropic_api_key:
        return "claude"
    if settings.azure_api_key:
        return "azure"
    return None


def azure_client(settings):
    from openai import OpenAI
    return OpenAI(api_key=settings.azure_api_key, base_url=settings.azure_openai_endpoint)


def azure_structured_call(client, settings, system_prompt: str, user_prompt: str) -> tuple[dict, int, int]:
    """One structured-output call to the Azure strong model.

    gpt-5-series models on Azure need two real, verified deviations from
    the standard OpenAI shape, both confirmed via live 400 responses
    rather than documentation: max_completion_tokens instead of
    max_tokens, and NO explicit temperature (0 is rejected outright —
    "Only the default (1) value is supported").
    """
    resp = client.chat.completions.create(
        model=settings.azure_strong_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=2048,
    )
    data = _parse_first_json_object(resp.choices[0].message.content)
    tokens_in = resp.usage.prompt_tokens if resp.usage else 0
    tokens_out = resp.usage.completion_tokens if resp.usage else 0
    return data, tokens_in, tokens_out


def _parse_first_json_object(content: str) -> dict:
    """json.loads() on the raw content is not safe here.

    Measured failure, intermittent: gpt-5.5 in JSON mode sometimes emits
    TWO concatenated JSON objects, which json.loads rejects wholesale
    with "Extra data: line 2 column 1". The first object is well-formed
    and is the answer; the trailing one is spillover. raw_decode() parses
    exactly the first value and reports where it stopped, so the
    spillover is ignored instead of taking down the whole agent run.
    """
    decoder = json.JSONDecoder()
    data, _end = decoder.raw_decode(content.strip())
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object, got {type(data).__name__}")
    return data
