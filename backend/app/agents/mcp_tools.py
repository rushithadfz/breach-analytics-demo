"""The MCP tool surface each agent exposes, built independently of a run.

Why this module exists: the four tool definitions used to be written
inline inside each agent's run function, and only inside the
`if backend == "claude"` branch. That made them unreachable — you could
not construct one, inspect its schema, or call its handler without an
Anthropic key and a live model. So the MCP surface was claimed in the
design doc and never exercised by anything, which is a claim resting on
code nobody had run.

Pulling the builders out here changes nothing at runtime — each agent
calls the same function it used to inline — and makes three things
testable without a network call: that the server constructs, that the
tool advertises the schema the prompt promises, and that the handler
records what the agent will later read.

Each builder returns `(server, sink)`. The sink is the list the handler
appends to; the agent already reads that list after the model turn, so
returning it explicitly makes the data flow visible rather than relying
on a closure the caller cannot see.
"""
from __future__ import annotations

from typing import Any, Callable


def _sdk():
    """Imported lazily: the Azure path never needs the SDK, and the
    original inline definitions were inside a backend check for the same
    reason. Importing at module scope would make the SDK a hard
    dependency of every agent run."""
    from claude_agent_sdk import create_sdk_mcp_server, tool
    return create_sdk_mcp_server, tool


def build_adjudicator_tools() -> tuple[Any, list[dict]]:
    """submit_adjudication — one entity-resolution verdict per pair."""
    create_sdk_mcp_server, tool = _sdk()
    recorded: list[dict] = []

    @tool(
        "submit_adjudication",
        "Record the entity-resolution decision for this candidate pair.",
        {"decision": str, "confidence": float, "rationale": str},
    )
    async def submit_adjudication(args: dict) -> dict:
        recorded.append(args)
        return {"content": [{"type": "text",
                             "text": f"Recorded: {args['decision']} (confidence {args['confidence']})"}]}

    return create_sdk_mcp_server(name="adjudicator_tools", tools=[submit_adjudication]), recorded


def build_investigator_tools() -> tuple[Any, list[dict]]:
    """record_investigation — a recovery strategy and its outcome."""
    create_sdk_mcp_server, tool = _sdk()
    recorded: list[dict] = []

    @tool(
        "record_investigation",
        "Record the diagnosis and recommended strategy for this quarantined document.",
        {"strategy": str, "diagnosis": str, "confidence": float},
    )
    async def record_investigation(args: dict) -> dict:
        recorded.append(args)
        return {"content": [{"type": "text", "text": f"Recorded: {args['strategy']}"}]}

    return create_sdk_mcp_server(name="investigator_tools", tools=[record_investigation]), recorded


def build_orchestrator_tools() -> tuple[Any, list[dict]]:
    """record_plan — what to run next, and whether a failure is systemic."""
    create_sdk_mcp_server, tool = _sdk()
    recorded: list[dict] = []

    @tool(
        "record_plan",
        "Record the processing plan for the next phase of the campaign.",
        {"next_agent": str, "scope": str, "reasoning": str,
         "infrastructure_escalations": str},
    )
    async def record_plan(args: dict) -> dict:
        recorded.append(args)
        return {"content": [{"type": "text", "text": f"Plan recorded: {args['next_agent']}"}]}

    return create_sdk_mcp_server(name="orchestrator_tools", tools=[record_plan]), recorded


def build_auditor_tools() -> tuple[Any, list[dict]]:
    """record_audit — whether a sampled flag survives re-verification."""
    create_sdk_mcp_server, tool = _sdk()
    recorded: list[dict] = []

    @tool(
        "record_audit",
        "Record whether this flag is supported by its cited evidence.",
        {"verdict": str, "confidence": float, "reasoning": str},
    )
    async def record_audit(args: dict) -> dict:
        recorded.append(args)
        return {"content": [{"type": "text", "text": f"Audit recorded: {args['verdict']}"}]}

    return create_sdk_mcp_server(name="auditor_tools", tools=[record_audit]), recorded


#: Every surface, for the tests and for anything that wants to enumerate
#: what this system exposes over MCP.
BUILDERS: dict[str, Callable[[], tuple[Any, list[dict]]]] = {
    "adjudicator_tools": build_adjudicator_tools,
    "investigator_tools": build_investigator_tools,
    "orchestrator_tools": build_orchestrator_tools,
    "auditor_tools": build_auditor_tools,
}

#: The fully-qualified names the agents pass as `allowed_tools`. MCP
#: namespaces every tool as mcp__<server>__<tool>, and a mismatch here is
#: silent: the model simply never gets offered the tool.
QUALIFIED_TOOL_NAMES = {
    "adjudicator_tools": "mcp__adjudicator_tools__submit_adjudication",
    "investigator_tools": "mcp__investigator_tools__record_investigation",
    "orchestrator_tools": "mcp__orchestrator_tools__record_plan",
    "auditor_tools": "mcp__auditor_tools__record_audit",
}
