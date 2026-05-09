"""
Psy-core Hook - Transform LLM output for cryptographic vetting.

Core design:
- Validate tool signatures
- Check execution permissions
- Audit logging
"""
import json
import hashlib
from typing import Dict, List, Any
from datetime import datetime


class PsyCoreHook:
    """
    Psy-core cryptographic audit hook.
    """

    # Trusted tool signatures (simplified - would be in DB)
    TRUSTED_SIGNATURES = {
        "browser": "a1b2c3d4",
        "code_interpreter": "e5f6g7h8",
        "kanban_tools": "i9j0k1l2",
    }

    def __init__(self, config: dict):
        """
        Initialize Psy-core hook.

        Config:
        - strict_mode: Block unverified tool calls (default: True)
        - audit_log_path: Path to audit log (default: ~/.hermes/psy-audit.log)
        """
        self.config = config
        self.strict_mode = config.get("strict_mode", True)
        self.audit_log_path = config.get("audit_log_path", "/tmp/psy-audit.log")

    def transform_llm_output(self, output: str) -> str:
        """
        Transform LLM output for cryptographic vetting.

        Args:
            output: Raw LLM output with tool calls

        Returns:
            Vetted output (same or with tool calls blocked)
        """
        # Parse tool calls (simplified - real implementation would parse structured format)
        tool_calls = self._parse_tool_calls(output)

        for tool_call in tool_calls:
            tool_name = tool_call.get("name", "")

            # Validate signature
            signature = self._calculate_signature(tool_name)
            trusted_sig = self.TRUSTED_SIGNATURES.get(tool_name)

            if signature != trusted_sig:
                self._log_audit(tool_name, signature, "SIGNATURE_MISMATCH")

                if self.strict_mode:
                    # Block tool call
                    output = output.replace(str(tool_call), "[BLOCKED TOOL: Signature mismatch]")
                else:
                    # Allow with warning
                    self._log_audit(tool_name, signature, "UNVERIFIED_TOOL_ALLOWED")
            else:
                self._log_audit(tool_name, signature, "VERIFIED")

        return output

    def _parse_tool_calls(self, output: str) -> List[Dict]:
        """
        Parse tool calls from LLM output.

        Simplified - real implementation would parse proper format.
        """
        # Placeholder: look for "call_tool" patterns
        tool_calls = []

        if "call_tool" in output:
            # Extract tool name (simplified)
            lines = output.split("\n")
            for line in lines:
                if "call_tool" in line and "{" in line:
                    try:
                        # Extract JSON
                        json_start = line.find("{")
                        json_str = line[json_start:]
                        data = json.loads(json_str)

                        tool_calls.append({
                            "name": data.get("tool", ""),
                            "args": data.get("args", {}),
                        })
                    except:
                        pass

        return tool_calls

    def _calculate_signature(self, tool_name: str) -> str:
        """
        Calculate tool signature (SHA256).

        Simplified - real implementation would use proper key.
        """
        return hashlib.sha256(tool_name.encode()).hexdigest()[:8]

    def _log_audit(self, tool_name: str, signature: str, status: str):
        """
        Log tool call for audit trail.
        """
        timestamp = datetime.now().isoformat()

        log_entry = f"{timestamp} | {tool_name} | {signature} | {status}\n"

        with open(self.audit_log_path, "a") as f:
            f.write(log_entry)

        print(f"[Psy-core] Audit: {tool_name} = {status}")


def register_hooks(plugin_api):
    """
    Register Psy-core hooks with Hermes.
    """
    hook = PsyCoreHook(plugin_api.config)

    # Register transform_llm_output hook
    plugin_api.register_hook("transform_llm_output", hook.transform_llm_output)

    print("[Psy-core] Cryptographic audit hook registered")
