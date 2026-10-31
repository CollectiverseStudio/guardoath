"""Bounded lexical indicators of agent workflow abuse (not semantic proof)."""
from __future__ import annotations
from dataclasses import dataclass
import re
import unicodedata
MAX_CHARS = 65536
@dataclass(frozen=True)
class Signal:
    rule_id: str
    severity: str = "high"
RULES = {
"workflow.approval_bypass": r"\b(?:skip|bypass|ignore)\b.{0,45}\b(?:human approval|approval gate|manual review)\b",
"workflow.forged_approval": r"\b(?:approval|authorization)\b.{0,30}\b(?:was|has been)\b.{0,20}\b(?:granted|preapproved)\b",
"workflow.disable_audit": r"\b(?:disable|suppress|turn off)\b.{0,35}\b(?:audit trail|audit logging|activity logging)\b",
"workflow.delete_evidence": r"\b(?:delete|erase|purge)\b.{0,35}\b(?:audit evidence|execution history|activity history)\b",
"workflow.skip_validation": r"\b(?:skip|bypass|disable)\b.{0,30}\b(?:input validation|output validation|policy checks)\b",
"workflow.override_tenant": r"\b(?:override|ignore|bypass)\b.{0,35}\b(?:tenant policy|tenant isolation|tenant restrictions)\b",
"workflow.cross_tenant": r"\b(?:read|export|access|copy)\b.{0,35}\b(?:another tenant|other tenant|all tenants)\b.{0,30}\b(?:data|records|secrets|keys)\b",
"workflow.impersonate_user": r"\b(?:impersonate|act as|assume identity of)\b.{0,30}\b(?:another user|administrator|account owner)\b",
"workflow.disable_mfa": r"\b(?:disable|turn off|remove|bypass)\b.{0,30}\b(?:mfa|multi.factor authentication|2fa)\b",
"workflow.session_hijack": r"\b(?:steal|reuse|hijack|extract)\b.{0,35}\b(?:session cookie|session token|auth cookie)\b",
"workflow.webhook_exfil": r"\b(?:send|forward|post|upload)\b.{0,35}\b(?:credentials|secrets|tokens)\b.{0,45}\bwebhook\b",
"workflow.disable_rate_limit": r"\b(?:disable|bypass|remove)\b.{0,30}\b(?:rate limit|request quota|throttling)\b",
"workflow.unlimited_recursion": r"\b(?:remove|disable|ignore)\b.{0,35}\b(?:recursion limit|max(?:imum)? depth|agent depth limit)\b",
"workflow.unbounded_spend": r"\b(?:remove|disable|ignore)\b.{0,35}\b(?:spending cap|cost limit|token budget)\b",
"workflow.tool_whitelist": r"\b(?:bypass|ignore|disable)\b.{0,35}\b(?:tool allowlist|tool whitelist|allowed tools)\b",
"workflow.shell_allow": r"\b(?:enable|allow|permit)\b.{0,35}\b(?:unrestricted shell|arbitrary shell commands|unrestricted command execution)\b",
"workflow.disable_sandbox": r"\b(?:disable|escape|bypass|turn off)\b.{0,35}\b(?:sandbox|container isolation)\b",
"workflow.publish_private": r"\b(?:publish|expose|make public)\b.{0,40}\b(?:private repository|internal documents|confidential files)\b",
"workflow.remove_retention": r"\b(?:disable|remove|bypass)\b.{0,35}\b(?:retention policy|legal hold|data retention controls)\b",
"workflow.disable_dlp": r"\b(?:disable|bypass|turn off)\b.{0,35}\b(?:dlp|data loss prevention|exfiltration detection)\b",
}
COMPILED={k:re.compile(v,re.I|re.S) for k,v in RULES.items()}
def inspect_text(content: str)->tuple[Signal,...]:
    if not isinstance(content,str):
        raise TypeError("content must be str")
    if len(content)>MAX_CHARS:
        raise ValueError("content too long")
    normalized=unicodedata.normalize("NFKC",content)
    if len(normalized)>MAX_CHARS*4:
        raise ValueError("normalized content too long")
    return tuple(Signal(k) for k,regex in COMPILED.items() if regex.search(normalized))
