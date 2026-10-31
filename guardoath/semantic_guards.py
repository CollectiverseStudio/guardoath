"""Deterministic, bounded text safety signals for GuardOath.

Lexical indicators are not semantic intent; host applies policy. No input is returned.
"""
from __future__ import annotations
from dataclasses import dataclass
import re
import unicodedata

MAX_CHARS = 65536

@dataclass(frozen=True)
class Signal:
    rule_id: str
    severity: str = "medium"

RULES = {
    "prompt.extract_system": r"\b(?:reveal|print|show|dump|repeat)\b.{0,55}\b(?:system prompt|developer instructions|hidden instructions)\b",
    "prompt.ignore_instructions": r"\bignore\b.{0,35}\b(?:previous|prior|above|system|developer)\b.{0,25}\b(?:instructions|rules|messages|prompts)\b",
    "prompt.role_spoof": r"\[(?:im_start|start_header_id)\](?:system|developer)\b|<\|(?:im_start|start_header_id)\|>\s*(?:system|developer)",
    "prompt.authority_spoof": r"\b(?:i am|acting as)\b.{0,30}\b(?:system administrator|platform administrator|developer)\b.{0,50}\b(?:override|ignore|disable)\b",
    "prompt.safety_bypass": r"\b(?:disable|bypass|turn off)\b.{0,35}\b(?:safety|moderation|guardrails|security checks)\b",
    "prompt.secret_request": r"\b(?:send|reveal|print|expose|dump)\b.{0,45}\b(?:api keys?|passwords?|access tokens?|private keys?)\b",
    "prompt.tool_override": r"\b(?:ignore|override|bypass)\b.{0,35}\b(?:tool permissions?|tool restrictions?|authorization checks?)\b",
    "prompt.exfiltrate": r"\b(?:upload|send|post|transmit)\b.{0,50}\b(?:secrets?|credentials?|private data|customer data)\b.{0,50}\b(?:to|endpoint|webhook)\b",
    "prompt.forged_policy": r"\b(?:new|updated|revised)\b.{0,25}\b(?:system policy|developer policy|security policy)\b.{0,35}\b(?:allows?|requires?|overrides?)\b",
    "prompt.chain_of_thought": r"\b(?:reveal|print|show|dump)\b.{0,40}\b(?:chain.of.thought|private reasoning|hidden reasoning)\b",
    "agent.shell_destructive": r"(?<!\w)(?:rm\s+-rf\s+/|mkfs(?:\.[a-z0-9]+)?\s+/dev/|dd\s+if=/dev/zero\s+of=/dev/)",
    "agent.shell_download_execute": r"\b(?:curl|wget)\b.{0,100}\|\s*(?:sh|bash|zsh)\b",
    "agent.shell_reverse_shell": r"\b(?:bash\s+-i\s+>&\s*/dev/tcp/|nc\s+-e\s+/bin/(?:sh|bash))",
    "agent.shell_privilege": r"\b(?:chmod\s+777\s+/etc/|sudo\s+visudo|setenforce\s+0)\b",
    "agent.git_force": r"\bgit\s+push\s+(?:--force|-f)\b",
    "agent.sql_destructive": r"\b(?:drop\s+(?:table|database)|truncate\s+table)\s+[a-z_][a-z0-9_]*\b",
    "agent.cloud_public": r"\b(?:make|set|change)\b.{0,40}\b(?:s3 bucket|storage bucket)\b.{0,35}\bpublic\b",
    "agent.audit_disable": r"\b(?:disable|turn off|delete)\b.{0,35}\b(?:audit logs?|cloudtrail|security logging)\b",
    "agent.credential_export": r"\b(?:export|copy|download)\b.{0,35}\b(?:credentials?|private keys?|service account keys?)\b",
    "agent.permission_escalation": r"\b(?:grant|assign|give)\b.{0,40}\b(?:admin|administrator|root)\b.{0,30}\b(?:access|permissions?|role)\b",
}
COMPILED = {key: re.compile(pattern, re.I | re.S) for key, pattern in RULES.items()}

def inspect_text(content: str) -> tuple[Signal, ...]:
    if not isinstance(content, str):
        raise TypeError("content must be text")
    if len(content) > MAX_CHARS:
        raise ValueError("content exceeds maximum character budget")
    normalized = unicodedata.normalize("NFKC", content)
    if len(normalized) > MAX_CHARS * 4:
        raise ValueError("normalized content exceeds maximum budget")
    return tuple(Signal(rule_id=key, severity="high" if key.startswith("agent.") else "medium")
                 for key, regex in COMPILED.items() if regex.search(normalized))
