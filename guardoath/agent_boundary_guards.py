"""Agentic action boundary indicators, batch 5.

These are lexical review signals, not authorization decisions or runtime enforcement.
Every indicator has a positive example and a benign look-alike.
"""
from __future__ import annotations
import re
import unicodedata
from dataclasses import dataclass

MAX_CHARS = 32000

@dataclass(frozen=True)
class Indicator:
    rule_id: str
    severity: str = "high"

# key -> (regex, positive example, benign look-alike)
CASES = {
"agent5.credential_in_url": (r"https?://[^\s]{0,80}(?:token|api_key|password)=[^\s&]+", "https://host.invalid/?token=secret", "https://host.invalid/?page=2"),
"agent5.disable_aws_config": (r"\baws\s+configservice\s+(?:stop-configuration-recorder|delete-configuration-recorder)\b", "aws configservice stop-configuration-recorder", "aws configservice describe-configuration-recorders"),
"agent5.delete_cloudwatch_logs": (r"\baws\s+logs\s+delete-log-group\b", "aws logs delete-log-group --log-group-name audit", "aws logs describe-log-groups"),
"agent5.disable_guardduty": (r"\baws\s+guardduty\s+delete-detector\b", "aws guardduty delete-detector", "aws guardduty list-detectors"),
"agent5.disable_securityhub": (r"\baws\s+securityhub\s+disable-security-hub\b", "aws securityhub disable-security-hub", "aws securityhub describe-hub"),
"agent5.delete_iam_policy": (r"\baws\s+iam\s+delete-policy\b", "aws iam delete-policy --policy-arn example", "aws iam list-policies"),
"agent5.create_iam_access_key": (r"\baws\s+iam\s+create-access-key\b", "aws iam create-access-key --user-name bot", "aws iam list-access-keys"),
"agent5.detach_iam_mfa": (r"\baws\s+iam\s+deactivate-mfa-device\b", "aws iam deactivate-mfa-device", "aws iam list-mfa-devices"),
"agent5.delete_rds_snapshot": (r"\baws\s+rds\s+delete-db-snapshot\b", "aws rds delete-db-snapshot", "aws rds describe-db-snapshots"),
"agent5.disable_rds_deletion_protection": (r"\baws\s+rds\s+modify-db-instance\b[^\n]{0,160}--no-deletion-protection\b", "aws rds modify-db-instance --no-deletion-protection", "aws rds modify-db-instance --deletion-protection"),
"agent5.eks_public_endpoint": (r"\baws\s+eks\s+update-cluster-config\b[^\n]{0,160}endpointPublicAccess\s*[=:]\s*true", "aws eks update-cluster-config --resources-vpc-config endpointPublicAccess=true", "aws eks describe-cluster"),
"agent5.k8s_cluster_admin": (r"\bkubectl\s+create\s+clusterrolebinding\b[^\n]{0,180}\bcluster-admin\b", "kubectl create clusterrolebinding all --clusterrole cluster-admin", "kubectl get clusterrolebindings"),
"agent5.k8s_exec": (r"\bkubectl\s+exec\b", "kubectl exec pod -- sh", "kubectl get pods"),
"agent5.k8s_secret_read": (r"\bkubectl\s+get\s+secrets?\b[^\n]{0,120}(?:-o\s+(?:json|yaml)|--output[= ](?:json|yaml))", "kubectl get secrets -o json", "kubectl get secrets"),
"agent5.docker_host_mount": (r"\bdocker\s+run\b[^\n]{0,160}(?:-v|--volume)\s+/:/", "docker run -v /:/host alpine", "docker run -v /tmp:/work alpine"),
"agent5.docker_cap_sys_admin": (r"\bdocker\s+run\b[^\n]{0,160}--cap-add[= ]SYS_ADMIN\b", "docker run --cap-add SYS_ADMIN alpine", "docker run --cap-drop ALL alpine"),
"agent5.git_skip_ssl": (r"\bgit\s+-c\s+http\.sslVerify=false\b", "git -c http.sslVerify=false clone repo", "git -c http.sslVerify=true clone repo"),
"agent5.pip_trusted_host": (r"\bpip(?:3)?\s+install\b[^\n]{0,150}--trusted-host\b", "pip install x --trusted-host host", "pip install x --index-url https://host"),
"agent5.curl_basic_auth": (r"\bcurl\b[^\n]{0,120}(?:-u|--user)\s+[^\s:]+:[^\s]+", "curl -u alice:password https://host", "curl -u alice https://host"),
"agent5.ssh_strict_host_disable": (r"\bssh\b[^\n]{0,120}-o\s+StrictHostKeyChecking=no\b", "ssh -o StrictHostKeyChecking=no host", "ssh -o StrictHostKeyChecking=yes host"),
"agent5.chmod_world_write": (r"\bchmod\s+(?:-R\s+)?(?:777|a\+w)\s+/(?:etc|var|usr|opt)(?:/|\b)", "chmod 777 /etc", "chmod 755 /etc"),
"agent5.chown_root_recursive": (r"\bchown\s+-R\s+[^\s]+\s+/(?:etc|var|usr)(?:/|\b)", "chown -R user /etc", "chown user /tmp/file"),
"agent5.disable_apparmor": (r"\b(?:systemctl\s+stop\s+apparmor|aa-disable\s+/)", "systemctl stop apparmor", "systemctl status apparmor"),
"agent5.disable_selinux": (r"\b(?:setenforce\s+0|SELINUX=disabled)\b", "setenforce 0", "getenforce"),
"agent5.open_all_ports": (r"\b(?:ufw\s+allow\s+from\s+any\s+to\s+any|iptables\s+-P\s+INPUT\s+ACCEPT)\b", "iptables -P INPUT ACCEPT", "iptables -P INPUT DROP"),
"agent5.public_postgres": (r"\blisten_addresses\s*=\s*['\"]?\*['\"]?", "listen_addresses = '*'", "listen_addresses = 'localhost'"),
"agent5.public_redis": (r"\b(?:bind\s+0\.0\.0\.0|protected-mode\s+no)\b", "protected-mode no", "protected-mode yes"),
"agent5.mysql_skip_grants": (r"\bmysqld\b[^\n]{0,100}--skip-grant-tables\b", "mysqld --skip-grant-tables", "mysqld --defaults-file=config"),
"agent5.nginx_disable_auth": (r"\bauth_basic\s+off\s*;", "auth_basic off;", "auth_basic on;"),
"agent5.disable_csrf": (r"\b(?:csrf_protection|csrf_enabled)\s*[=:]\s*(?:false|0)\b", "csrf_enabled=false", "csrf_enabled=true"),
"agent5.disable_cors_origin": (r"\bAccess-Control-Allow-Origin\s*:\s*\*", "Access-Control-Allow-Origin: *", "Access-Control-Allow-Origin: https://site.invalid"),
"agent5.jwt_none_algorithm": (r"\b(?:algorithms?|algorithm)\s*[=:]\s*['\"]?none\b", "algorithm=none", "algorithm=RS256"),
"agent5.disable_webhook_signature": (r"\b(?:verify_webhook_signature|webhook_signature_verification)\s*[=:]\s*(?:false|0)\b", "verify_webhook_signature=false", "verify_webhook_signature=true"),
"agent5.disable_tls_cert": (r"\b(?:verify_ssl|verify_tls|verify_certificates)\s*[=:]\s*(?:false|0)\b", "verify_tls=false", "verify_tls=true"),
"agent5.disable_content_filter": (r"\b(?:content_filter_enabled|moderation_enabled)\s*[=:]\s*(?:false|0)\b", "moderation_enabled=false", "moderation_enabled=true"),
"agent5.disable_dlp_setting": (r"\b(?:dlp_enabled|data_loss_prevention)\s*[=:]\s*(?:false|0)\b", "dlp_enabled=false", "dlp_enabled=true"),
"agent5.disable_secret_scan": (r"\b(?:secret_scanning_enabled|secret_scan_enabled)\s*[=:]\s*(?:false|0)\b", "secret_scanning_enabled=false", "secret_scanning_enabled=true"),
"agent5.disable_audit_setting": (r"\b(?:audit_logging_enabled|audit_enabled)\s*[=:]\s*(?:false|0)\b", "audit_enabled=false", "audit_enabled=true"),
"agent5.disable_backup_setting": (r"\b(?:backups_enabled|backup_enabled)\s*[=:]\s*(?:false|0)\b", "backup_enabled=false", "backup_enabled=true"),
"agent5.disable_encryption_setting": (r"\b(?:encryption_at_rest|encrypt_at_rest)\s*[=:]\s*(?:false|0)\b", "encryption_at_rest=false", "encryption_at_rest=true"),
"agent5.disable_validation_setting": (r"\b(?:input_validation_enabled|output_validation_enabled)\s*[=:]\s*(?:false|0)\b", "input_validation_enabled=false", "input_validation_enabled=true"),
}
COMPILED = {name: re.compile(pattern, re.I) for name,(pattern,_,_) in CASES.items()}

def inspect(content: str) -> tuple[Indicator,...]:
    if not isinstance(content,str):
        raise TypeError("content must be text")
    if len(content)>MAX_CHARS:
        raise ValueError("input exceeds character budget")
    text=unicodedata.normalize("NFKC",content)
    if len(text)>MAX_CHARS*4:
        raise ValueError("normalized input exceeds budget")
    return tuple(Indicator(name) for name,rx in COMPILED.items() if rx.search(text))
