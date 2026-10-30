"""One record per tenant: account, key hashes, monthly usage, and one field per check.

A check field holds the action taken when that check finds something: block, flag, allow, or
redact (pii only). A check field with no value means the check does not run. The settings some
checks need are fields on the same record. They are kept while a check is off, so turning the
check back on restores them, and they are passed to the engine only while the check is on.

The record also names its default profile: "tenant" (the record's own checks, above) or one of the
example profiles the hosted service offers. A check request that names no profile uses it.
"""
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from guardoath.billing import BillingMode
from guardoath.models import (Action, ConfidentialTerm, Contract, EvidencePolicy, FieldComparison, GraphPolicy,
                              IntegrityPolicy, LedgerPolicy, MatchedOutcomes, Policy, RequiredSection, Rule,
                              Stage, TimestampPolicy, ToolPolicy)
from guardoath.privacy_catalog import DEFAULT_MIN_SCORE

# Every check a tenant can turn on. The simulated "safety" and "bias" checkers are test fixtures only.
CHECK_IDS = ("pii", "encoded_pii", "secrets", "text_security", "schema", "cross_field", "completeness",
             "matched_outcomes", "evidence", "tool_calls", "ledger", "timestamps", "graph", "integrity",
             "content_safety", "azure_prompt_shields")
# Checks that call a paid outside provider. They never run for demo users.
EXTERNAL_CHECK_IDS = frozenset({"content_safety", "azure_prompt_shields"})
INPUT_ONLY_CHECK_IDS = frozenset({"azure_prompt_shields"})
# A check listed here cannot run without at least one of these settings.
REQUIRED_SETTINGS = {
    "schema": ("input_schema", "output_schema"), "cross_field": ("comparisons",),
    "completeness": ("required_sections",), "matched_outcomes": ("matched_outcomes",),
    "evidence": ("evidence",), "tool_calls": ("tool_calls",), "ledger": ("ledger",),
    "timestamps": ("timestamps",), "graph": ("graph",), "integrity": ("integrity",),
}
# The settings each check uses. They are given to the engine only while that check is on.
SETTINGS_OF = {
    **REQUIRED_SETTINGS,
    "pii": ("detect_labeled_sensitive_data", "privacy_patterns", "disabled_privacy_entities", "privacy_min_score"),
    "secrets": ("detect_secret_assignments", "inspect_encoded_secrets", "confidential_terms"),
}
SETTING_FIELDS = tuple(dict.fromkeys(name for names in SETTINGS_OF.values() for name in names))
CHECK_FIELDS = tuple("check_" + check_id for check_id in CHECK_IDS)
# Fields a tenant (or a demo visitor, on a demo record) may change. Account fields are admin-only.
EDITABLE_FIELDS = frozenset({"mode", "timeout_ms", "failure_action", "default_profile", *CHECK_FIELDS, *SETTING_FIELDS})
TENANT_POLICY_VERSION = "tenant"
# Profile names a record can choose as its default. Which profiles exist is checked by the hosted service.
PROFILE_PATTERN = r"^[a-z][a-z0-9-]{0,99}$"
KEY_PREFIX = "gok_"
TENANT_ID_PATTERN = r"^[a-z][a-z0-9-]{1,39}$"
MONTH_PATTERN = r"^[0-9]{4}-(0[1-9]|1[0-2])$"
DEMO_DEFAULTS_FILE = Path(__file__).parent / "demo_tenant_defaults.json"

Month = Annotated[str, Field(pattern=MONTH_PATTERN)]
KeyHash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class TenantRecord(Contract):
    # Account
    tenant_id: str = Field(pattern=TENANT_ID_PATTERN)
    display_name: str = Field(min_length=1, max_length=100)
    status: Literal["active", "disabled"] = "active"
    demo: bool = Field(default=False, strict=True)
    key_hashes: tuple[KeyHash, ...] = Field(default=(), max_length=2)
    monthly_allowance: int = Field(default=0, ge=0, le=1_000_000_000, strict=True)
    overage: Literal["allow_and_record", "block"] = "allow_and_record"
    usage: dict[Month, Annotated[int, Field(ge=0, strict=True)]] = Field(default_factory=dict, max_length=120)
    billing_mode: BillingMode = BillingMode.INTERNAL
    payer_id: str | None = Field(default=None, min_length=1, max_length=100)
    revision: int = Field(default=0, ge=0, strict=True)
    updated_at: str | None = Field(default=None, max_length=40)
    # How the checks run
    mode: Literal["observe", "enforce"] = "enforce"
    timeout_ms: int = Field(default=1000, ge=1, le=5_000, strict=True)
    failure_action: Literal["block", "flag"] = "block"
    # The profile used when a check request names none: "tenant" = the check fields below
    default_profile: str = Field(default=TENANT_POLICY_VERSION, pattern=PROFILE_PATTERN)
    # One field per check: no value = the check does not run
    check_pii: Action | None = None
    check_encoded_pii: Action | None = None
    check_secrets: Action | None = None
    check_text_security: Action | None = None
    check_schema: Action | None = None
    check_cross_field: Action | None = None
    check_completeness: Action | None = None
    check_matched_outcomes: Action | None = None
    check_evidence: Action | None = None
    check_tool_calls: Action | None = None
    check_ledger: Action | None = None
    check_timestamps: Action | None = None
    check_graph: Action | None = None
    check_integrity: Action | None = None
    check_content_safety: Action | None = None
    check_azure_prompt_shields: Action | None = None
    # Check settings (same names and rules as Policy)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    comparisons: tuple[FieldComparison, ...] = Field(default=(), max_length=50)
    required_sections: tuple[RequiredSection, ...] = Field(default=(), max_length=50)
    detect_labeled_sensitive_data: bool = Field(default=False, strict=True)
    privacy_patterns: bool = Field(default=True, strict=True)
    disabled_privacy_entities: tuple[str, ...] = Field(default=(), max_length=100)
    privacy_min_score: float = Field(default=DEFAULT_MIN_SCORE, ge=0.0, le=1.0, allow_inf_nan=False)
    detect_secret_assignments: bool = Field(default=False, strict=True)
    inspect_encoded_secrets: bool = Field(default=False, strict=True)
    confidential_terms: tuple[ConfidentialTerm, ...] = Field(default=(), max_length=50)
    matched_outcomes: MatchedOutcomes | None = None
    evidence: EvidencePolicy | None = None
    tool_calls: ToolPolicy | None = None
    ledger: LedgerPolicy | None = None
    timestamps: TimestampPolicy | None = None
    graph: GraphPolicy | None = None
    integrity: IntegrityPolicy | None = None

    @model_validator(mode="after")
    def consistent(self):
        on = self.checks()
        if not on:
            raise ValueError("Turn on at least one check")
        for check_id, action in on.items():
            if action == Action.REDACT and check_id != "pii":
                raise ValueError(f"Only the pii check can redact; choose block, flag or allow for {check_id}")
        for check_id, names in REQUIRED_SETTINGS.items():
            if check_id in on and not any(getattr(self, name) for name in names):
                raise ValueError(f"The {check_id} check needs its settings before it can run: {', '.join(names)}")
        if self.demo:
            if on.keys() & EXTERNAL_CHECK_IDS:
                raise ValueError("Demo users cannot run checks that call a paid outside provider")
            if self.overage != "block":
                raise ValueError("Demo users have a hard monthly limit")
        # Field-level rules of every stored setting, including settings of checks that are off.
        Policy(application_id=self.tenant_id, version=TENANT_POLICY_VERSION, rules=(),
               **{name: getattr(self, name) for name in SETTING_FIELDS})
        return self

    def checks(self) -> dict[str, Action]:
        """The checks that run, with their actions, in CHECK_IDS order."""
        return {check_id: getattr(self, "check_" + check_id) for check_id in CHECK_IDS
                if getattr(self, "check_" + check_id) is not None}

    def to_policy(self) -> Policy:
        on = self.checks()
        settings = {name: getattr(self, name) for check_id, names in SETTINGS_OF.items()
                    if check_id in on for name in names}
        rules = tuple(Rule(checker_id=check_id, on_finding=action,
                           stages=(Stage.INPUT,) if check_id in INPUT_ONLY_CHECK_IDS else (Stage.INPUT, Stage.OUTPUT))
                      for check_id, action in on.items())
        return Policy(application_id=self.tenant_id, version=TENANT_POLICY_VERSION, mode=self.mode,
                      timeout_ms=self.timeout_ms, failure_action=self.failure_action, rules=rules,
                      external_checks="after_local_pass" if on.keys() & EXTERNAL_CHECK_IDS else "disabled",
                      **settings)

    def used(self, month: str) -> int:
        return self.usage.get(month, 0)


def usage_month(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m")


def key_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_key(tenant_id: str) -> tuple[str, str]:
    """A new key and its hash. Only the hash is stored; the key is shown once."""
    if not re.fullmatch(TENANT_ID_PATTERN, tenant_id):
        raise ValueError("Invalid tenant identifier")
    token = f"{KEY_PREFIX}{tenant_id}_{secrets.token_urlsafe(32)}"
    return token, key_hash(token)


def tenant_id_from_key(token: str) -> str | None:
    """The tenant a key names, or None for anything that is not a well-formed key."""
    if not isinstance(token, str) or len(token) > 200 or not token.startswith(KEY_PREFIX):
        return None
    tenant_id, separator, secret = token[len(KEY_PREFIX):].partition("_")
    if not separator or len(secret) < 32 or not re.fullmatch(TENANT_ID_PATTERN, tenant_id):
        return None
    return tenant_id


def key_matches(record: TenantRecord, token: str) -> bool:
    digest = key_hash(token)
    matched = False
    for stored in record.key_hashes:
        matched |= hmac.compare_digest(digest, stored)
    return matched


MIN_DEMO_KEY_SECRET = 32


def demo_key(tenant_id: str, secret: str) -> str:
    """The API key of a demo user. It is derived from a server secret, so every visitor who chooses
    the same demo user gets the same key and no key is stored; the record keeps only its hash."""
    if not re.fullmatch(TENANT_ID_PATTERN, tenant_id):
        raise ValueError("Invalid tenant identifier")
    if not isinstance(secret, str) or len(secret) < MIN_DEMO_KEY_SECRET:
        raise ValueError(f"The demo key secret must be at least {MIN_DEMO_KEY_SECRET} characters")
    digest = hmac.new(secret.encode(), b"guardoath-demo-key\x00" + tenant_id.encode(), hashlib.sha256).digest()
    return f"{KEY_PREFIX}{tenant_id}_" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def demo_records(path: Path = DEMO_DEFAULTS_FILE, key_secret: str | None = None) -> tuple[TenantRecord, ...]:
    """demo1..demoN, each an ordinary tenant record created from the demo defaults. With a key secret,
    each record holds the hash of its derived demo key."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) - {"format_version", "_comment", "count", "monthly_allowance", "defaults"}:
        raise ValueError("Unknown demo defaults format")
    if raw.get("format_version") != "1":
        raise ValueError("Unsupported demo defaults version")
    count, allowance, defaults = raw["count"], raw["monthly_allowance"], raw["defaults"]
    if type(count) is not int or not 1 <= count <= 100:
        raise ValueError("Demo user count must be 1 to 100")
    if not isinstance(defaults, dict) or set(defaults) - EDITABLE_FIELDS:
        raise ValueError("Demo defaults may only set check fields and check settings")
    return tuple(TenantRecord(tenant_id=f"demo{n}", display_name=f"Demo user {n}", demo=True,
                              monthly_allowance=allowance, overage="block",
                              key_hashes=(key_hash(demo_key(f"demo{n}", key_secret)),) if key_secret else (),
                              **defaults)
                 for n in range(1, count + 1))


def demo_defaults(path: Path = DEMO_DEFAULTS_FILE) -> dict:
    """The editable values a demo user is reset to. Fields not listed are cleared."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    fields = TenantRecord.model_fields
    values = {name: fields[name].get_default(call_default_factory=True) for name in EDITABLE_FIELDS}
    values.update(raw["defaults"])
    return values
