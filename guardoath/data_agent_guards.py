"""Bounded deterministic signals for data exfiltration and agent policy abuse."""
from __future__ import annotations
from dataclasses import dataclass
import re
import unicodedata
MAX_CHARS = 65536
@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: str
RULES = {
"data.sql_unrestricted_export": r"\b(?:select\s+\*\s+from\s+(?:customers|patients|users)\b.{0,80}\b(?:into\s+outfile|copy\s+to)|copy\s+\(\s*select\b.{0,80}\)\s+to\b)",
"data.sql_disable_rls": r"\balter\s+table\s+[\w.]+\s+disable\s+row\s+level\s+security\b",
"data.sql_disable_triggers": r"\balter\s+table\s+[\w.]+\s+disable\s+trigger\s+all\b",
"data.sql_grant_public": r"\bgrant\s+(?:all(?:\s+privileges)?|select|insert|update|delete)\s+on\b.{0,80}\bto\s+public\b",
"data.sql_delete_unbounded": r"\bdelete\s+from\s+[\w.]+\s*;",
"data.sql_update_unbounded": r"\bupdate\s+[\w.]+\s+set\s+\w+\s*=.{0,90};",
"data.mongo_drop_database": r"\bdb\s*\.\s*dropDatabase\s*\(",
"data.mongo_unbounded_delete": r"\b(?:deleteMany|remove)\s*\(\s*\{\s*\}\s*\)",
"data.redis_flushall": r"\b(?:redis-cli\s+)?flushall\b",
"data.redis_config_public": r"\bconfig\s+set\s+protected-mode\s+no\b",
"agent.git_secret_commit": r"\bgit\s+add\s+[^\n]{0,80}(?:\.env\b|id_rsa\b|credentials\.json\b)",
"agent.git_disable_hooks": r"\bgit\s+-c\s+core\.hooksPath\s*=\s*/dev/null\b",
"agent.ci_skip_security": r"\b(?:skip|disable|remove)\b.{0,50}\b(?:security scan|secret scanning|sast|dependency scan)\b",
"agent.ci_untrusted_secrets": r"\b(?:expose|pass|send)\b.{0,55}\b(?:secrets|tokens|credentials)\b.{0,55}\b(?:pull requests? from forks?|untrusted jobs?)\b",
"agent.browser_cookie_export": r"\b(?:export|upload|send|copy)\b.{0,55}\b(?:browser cookies|session cookies|session tokens)\b",
"agent.browser_disable_samesite": r"\b(?:disable|remove|set)\b.{0,45}\bsamesite\b.{0,30}\b(?:none|off|false)\b",
"agent.oauth_wildcard_redirect": r"\b(?:allow|set|configure)\b.{0,55}\b(?:oauth|oidc)\b.{0,55}\b(?:wildcard|any redirect|all redirect)\b",
"agent.jwt_skip_signature": r"\b(?:disable|skip|bypass)\b.{0,40}\b(?:jwt|token)\b.{0,30}\b(?:signature|verification)\b",
"agent.logging_sensitive": r"\b(?:log|print|dump)\b.{0,45}\b(?:authorization header|bearer token|session cookie|private key)\b",
"agent.backup_delete": r"\b(?:delete|purge|remove)\b.{0,45}\b(?:all backups|recovery snapshots|database backups)\b",
}
COMPILED = {k: re.compile(v, re.IGNORECASE) for k,v in RULES.items()}
def inspect_text(content: str) -> tuple[Finding,...]:
    if not isinstance(content,str):
        raise TypeError("content must be str")
    if len(content)>MAX_CHARS:
        raise ValueError("input exceeds size limit")
    normalized=unicodedata.normalize("NFKC",content)
    if len(normalized)>MAX_CHARS*4:
        raise ValueError("normalized input exceeds size limit")
    return tuple(Finding(k,"high") for k,regex in COMPILED.items() if regex.search(normalized))
