"""
Psy-core Hook — Tool execution allowlist and audit logging.

Replaces fake cryptographic signatures with a simple tool allowlist.
All tool calls are logged for audit trail using Python's logging module.
"""
import json
import logging
from typing import Dict, List
from datetime import datetime

logger = logging.getLogger("psy_core")

# Simple allowlist of permitted tools (replaces fake signature system)
ALLOWED_TOOLS = frozenset({
    "browser",
    "code_interpreter",
    "kanban_tools",
})


class PsyCoreHook:
    """Tool allowlist and audit hook for Hermes."""

    def __init__(self, config: dict):
        self.strict_mode = config.get("strict_mode", True)
        self.audit_log_path = config.get("audit_log_path")

        # Set up file-based audit logging if path configured
        if self.audit_log_path:
            handler = logging.FileHandler(self.audit_log_path)
            handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
            logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    def transform_llm_output(self, output: str) -> str:
        """Transform LLM output, blocking unauthorized tool calls."""
        tool_calls = self._parse_tool_calls(output)

        for tool_call in tool_calls:
            tool_name = tool_call.get("name", "")

            if tool_name not in ALLOWED_TOOLS:
                logger.warning("BLOCKED tool: %s (not in allowlist)", tool_name)
                if self.strict_mode:
                    output = output.replace(
                        str(tool_call),
                        f"[BLOCKED: {tool_name} not in allowlist]",
                    )
            else:
                logger.info("ALLOWED tool: %s", tool_name)

        return output

    def _parse_tool_calls(self, output: str) -> List[Dict]:
        """Parse tool calls from LLM output."""
        tool_calls = []
        if "call_tool" in output:
            lines = output.split("\n")
            for line in lines:
                if "call_tool" in line and "{" in line:
                    try:
                        json_start = line.find("{")
                        data = json.loads(line[json_start:])
                        tool_calls.append({
                            "name": data.get("tool", ""),
                            "args": data.get("args", {}),
                        })
                    except (json.JSONDecodeError, ValueError) as e:
                        logger.debug("Failed to parse tool call: %s", e)
        return tool_calls


def register_hooks(plugin_api):
    """Register Psy-core hooks with Hermes."""
    hook = PsyCoreHook(plugin_api.config)
    plugin_api.register_hook("transform_llm_output", hook.transform_llm_output)
    logger.info("Psy-core audit hook registered (allowlist mode)")
