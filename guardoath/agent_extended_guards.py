"""Additional bounded lexical signals; not a runtime sandbox."""
from __future__ import annotations
from dataclasses import dataclass
import re
from .semantic_guards import MAX_CHARS

@dataclass(frozen=True)
class ExtendedSignal:
    rule_id: str
    severity: str = "high"

RULES = {
 "agent.kubernetes_privileged": r"\bprivileged\s*:\s*true\b",
 "agent.kubernetes_hostnetwork": r"\bhostNetwork\s*:\s*true\b",
 "agent.kubernetes_hostpid": r"\bhostPID\s*:\s*true\b",
 "agent.docker_privileged": r"\bdocker\s+run\b[^\n]{0,200}\s--privileged\b",
 "agent.docker_socket_mount": r"\b(?:docker\s+run|volumes\s*:)\b[^\n]{0,200}/var/run/docker\.sock",
 "agent.terraform_destroy": r"\bterraform\s+destroy\b",
 "agent.kubernetes_delete_namespace": r"\bkubectl\s+delete\s+namespace\s+\S+",
 "agent.kubernetes_delete_all": r"\bkubectl\s+delete\s+all\s+--all\b",
 "agent.aws_disable_cloudtrail": r"\baws\s+cloudtrail\s+(?:stop-logging|delete-trail)\b",
 "agent.aws_delete_kms_key": r"\baws\s+kms\s+schedule-key-deletion\b",
 "agent.aws_public_bucket_acl": r"\baws\s+s3api\s+put-bucket-acl\b[^\n]{0,200}\bpublic-read\b",
 "agent.aws_delete_bucket": r"\baws\s+s3\s+rb\s+s3://\S+\s+--force\b",
 "agent.gcp_public_iam": r"\bgcloud\b[^\n]{0,200}\badd-iam-policy-binding\b[^\n]{0,200}\ballUsers\b",
 "agent.azure_public_blob": r"\baz\s+storage\s+container\s+set-permission\b[^\n]{0,200}\bpublic-access\s+(?:blob|container)\b",
 "agent.firewall_disable": r"\b(?:ufw\s+disable|systemctl\s+stop\s+firewalld|iptables\s+-F)\b",
 "agent.ssh_root_enable": r"\bPermitRootLogin\s+yes\b",
 "agent.tls_verify_disable": r"\b(?:verify\s*=\s*False|curl\s+(?:-k|--insecure)\b|NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*0)\b",
 "agent.npm_ignore_scripts": r"\bnpm\s+config\s+set\s+ignore-scripts\s+false\b",
 "agent.git_hooks_disable": r"\bHUSKY\s*=\s*0\b|\bgit\s+-c\s+core\.hooksPath\s*=\s*/dev/null\b",
 "agent.db_grant_all": r"\bGRANT\s+ALL\s+PRIVILEGES\s+ON\s+\*\.\*\s+TO\b",
}
COMPILED = {key: re.compile(pattern, re.IGNORECASE) for key, pattern in RULES.items()}

def inspect_extended(content: str) -> tuple[ExtendedSignal, ...]:
    if not isinstance(content, str):
        raise TypeError("content must be text")
    if len(content) > MAX_CHARS:
        raise ValueError("content exceeds maximum character budget")
    return tuple(ExtendedSignal(key) for key, pattern in COMPILED.items() if pattern.search(content))
