"""One record per tenant: account, key hashes, monthly usage, and two fields per check (input and output).

A check field (input_check_<id>, output_check_<id>) holds the action taken when that check finds something on that
stage: block, flag, allow, or redact (pii only). A check field with no value means the check does not run on that
stage, so a check can run on input only, output only, or both, with a different action on each (2026-11-04; before,
one check_<id> field covered both stages, and such records are converted when read). The settings some
checks need are fields on the same record. They are kept while a check is off, so turning the
check back on restores them, and they are passed to the engine only while the check is on.

A check whose settings cover one stage only runs on that stage (2026-11-04): schema runs on the stages that have a
schema (input_schema, output_schema), cross_field on the stages of its comparisons, completeness on the stages of
its required sections. Before, it ran on both, and every message on the other stage came back incomplete (blocked).
Every other check runs on both stages.

Each field comparison and required section on a record is a rule of that record only (business.<rule_id>,
completeness.<rule_id>; guardoath.record_rules), and can be set on its own, per stage, like a catalog rule.

One rule inside a check can be set on its own, per stage, in input_rule_actions and output_rule_actions: "off" stops that rule, and block, flag or
allow replace the check's action for that rule only. Rule ids are the ones in guardoath.rule_catalog.

Every check request runs the checks on its tenant's record. There are no example profiles for tenants
(removed 2026-11-02). Records stored before then may still hold "default_profile": it is dropped when the
record is read, except on a customer (non-demo) record that names an example profile: that record is
refused until it is migrated, so its checks never change without review.
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
from guardoath.models import (Action, AdvancedArithmeticRule, AdvancedIdentityRule, AdvancedConversionTimeRule, AdvancedTrustedReferenceRule, AdvancedStatisticalDriftRule, ConfidentialTerm, Contract, EvidencePolicy, FieldComparison, GraphPolicy,
                              IntegrityPolicy, LedgerPolicy, MatchedOutcomes, Policy, RequiredSection, Rule,
                              Stage, TimestampPolicy, ToolPolicy)
from guardoath.privacy_catalog import DEFAULT_MIN_SCORE
from guardoath.record_rules import bad_record_rule_ids, record_rule_ids
from guardoath.rule_catalog import RULE_ID_PATTERN, RULES

# Every check a tenant can turn on. GuardOath runs every one of them itself: no content is sent to another company's
# service (Greg, 2026-11-02 and 2026-11-05). The simulated "safety" and "bias" checkers are test fixtures only.
CHECK_IDS = ("pii", "encoded_pii", "secrets", "text_security", "prompt_attack", "lexical_guards", "human_safety",
             "harmful_content", "schema", "cross_field", "completeness", "matched_outcomes", "evidence", "tool_calls",
             "ledger", "timestamps", "graph", "integrity", "csv_formula")
# The two checks that sent content to an outside provider were removed on 2026-11-05: content_safety (Amazon Bedrock
# Guardrails) and azure_prompt_shields (Azure Prompt Shields). harmful_content and prompt_attack do that work here.
# Stored records still hold their fields (every field is stored, null when off); they are dropped when read.
RETIRED_CHECK_IDS = ("content_safety", "azure_prompt_shields")
RETIRED_CHECK_FIELDS = frozenset(f"{prefix}{check_id}" for check_id in RETIRED_CHECK_IDS
                                 for prefix in ("check_", "input_check_", "output_check_"))
# A check listed here cannot run without at least one of these settings.
REQUIRED_SETTINGS = {
    "schema": ("input_schema", "output_schema"), "cross_field": ("comparisons", "advanced_arithmetic", "advanced_identity", "advanced_conversion_time", "advanced_trusted_reference", "advanced_statistical_drift"),
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
STAGES = (Stage.INPUT, Stage.OUTPUT)


def check_field(check_id: str, stage: Stage | str) -> str:
    """The record field that holds a check's action on one stage: input_check_<id> or output_check_<id>."""
    return f"{Stage(stage).value}_check_{check_id}"


def rule_actions_field(stage: Stage | str) -> str:
    return f"{Stage(stage).value}_rule_actions"


INPUT_CHECK_FIELDS = tuple(check_field(check_id, Stage.INPUT) for check_id in CHECK_IDS)
OUTPUT_CHECK_FIELDS = tuple(check_field(check_id, Stage.OUTPUT) for check_id in CHECK_IDS)
CHECK_FIELDS = INPUT_CHECK_FIELDS + OUTPUT_CHECK_FIELDS
RULE_ACTION_FIELDS = (rule_actions_field(Stage.INPUT), rule_actions_field(Stage.OUTPUT))
# Field names of records stored before 2026-11-04 (one per check, for both stages); converted when read.
LEGACY_FIELDS = frozenset({*("check_" + check_id for check_id in CHECK_IDS), "rule_actions"})
# Fields a tenant (or a demo visitor, on a demo record) may change. Account fields are admin-only.
EDITABLE_FIELDS = frozenset({"mode", "timeout_ms", "failure_action", "sigma_rule_actions", "input_attachment_csv_mode", "output_attachment_csv_mode", *RULE_ACTION_FIELDS, *CHECK_FIELDS, *SETTING_FIELDS})
TENANT_POLICY_VERSION = "tenant"
# A record is stored as one DynamoDB item, which holds at most 400 KB (attribute names and values). The record's own
# fields (everything but usage and revision, as guardoath.dynamodb_store.record_json stores them) are kept below this,
# which leaves room for the item's usage counts, revision and key. Checked when a change is saved (hosted).
MAX_RECORD_BYTES = 350_000
# The retired record field that named a default example profile (removed 2026-11-02).
RETIRED_PROFILE_FIELD = "default_profile"
KEY_PREFIX = "gok_"
TENANT_ID_PATTERN = r"^[a-z][a-z0-9-]{1,39}$"
MONTH_PATTERN = r"^[0-9]{4}-(0[1-9]|1[0-2])$"
DEMO_DEFAULTS_FILE = Path(__file__).parent / "demo_tenant_defaults.json"

Month = Annotated[str, Field(pattern=MONTH_PATTERN)]
RuleActions = dict[Annotated[str, Field(pattern=RULE_ID_PATTERN, max_length=100)], Literal["off", "allow", "flag", "block"]]
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
    # Bound only by server-side subscription reconciliation, never user-editable.
    # Existing tenants have None until independently verified with Stripe.
    stripe_subscription_id: str | None = Field(default=None, pattern=r"^sub_[A-Za-z0-9_]+$", max_length=100)
    # None is legacy-record migration state, NOT evidence of a paid subscription.
    # Newly registered accounts explicitly start inactive. Only the trusted
    # billing reconciler/admin can write entitlement; it is not EDITABLE_FIELDS.
    subscription_status: Literal["inactive", "active", "past_due", "canceled"] | None = None
    revision: int = Field(default=0, ge=0, strict=True)
    updated_at: str | None = Field(default=None, max_length=40)
    # How the checks run
    mode: Literal["observe", "enforce"] = "enforce"
    timeout_ms: int = Field(default=1000, ge=1, le=5_000, strict=True)
    failure_action: Literal["block", "flag"] = "block"
    # One field per check and stage (2026-11-04): no value = the check does not run on that stage.
    input_check_pii: Action | None = None
    input_check_encoded_pii: Action | None = None
    input_check_secrets: Action | None = None
    input_check_text_security: Action | None = None
    input_check_prompt_attack: Action | None = None
    input_check_lexical_guards: Action | None = None
    input_check_human_safety: Action | None = None
    input_check_harmful_content: Action | None = None
    input_check_schema: Action | None = None
    input_check_cross_field: Action | None = None
    input_check_completeness: Action | None = None
    input_check_matched_outcomes: Action | None = None
    input_check_evidence: Action | None = None
    input_check_tool_calls: Action | None = None
    input_check_ledger: Action | None = None
    input_check_timestamps: Action | None = None
    input_check_graph: Action | None = None
    input_check_integrity: Action | None = None
    input_check_csv_formula: Action | None = None
    # Attachment inspection activates only when bytes are submitted. Missing fields on
    # new AND legacy records mean Auto; only a persisted explicit "off" disables it.
    input_attachment_csv_mode: Literal["auto", "off"] = "auto"
    output_attachment_csv_mode: Literal["auto", "off"] = "auto"
    output_check_pii: Action | None = None
    output_check_encoded_pii: Action | None = None
    output_check_secrets: Action | None = None
    output_check_text_security: Action | None = None
    output_check_prompt_attack: Action | None = None
    output_check_lexical_guards: Action | None = None
    output_check_human_safety: Action | None = None
    output_check_harmful_content: Action | None = None
    output_check_schema: Action | None = None
    output_check_cross_field: Action | None = None
    output_check_completeness: Action | None = None
    output_check_matched_outcomes: Action | None = None
    output_check_evidence: Action | None = None
    output_check_tool_calls: Action | None = None
    output_check_ledger: Action | None = None
    output_check_timestamps: Action | None = None
    output_check_graph: Action | None = None
    output_check_integrity: Action | None = None
    output_check_csv_formula: Action | None = None
    # Check settings (same names and rules as Policy)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    # No count limit (2026-11-05, Greg): the record's size is bounded instead (MAX_RECORD_BYTES, checked when a change
    # is saved) and a save's request body by the API's JSON limit.
    comparisons: tuple[FieldComparison, ...] = ()
    advanced_arithmetic: tuple[AdvancedArithmeticRule, ...] = ()
    advanced_identity: tuple[AdvancedIdentityRule, ...] = ()
    advanced_conversion_time: tuple[AdvancedConversionTimeRule, ...] = ()
    advanced_trusted_reference: tuple[AdvancedTrustedReferenceRule, ...] = ()
    advanced_statistical_drift: tuple[AdvancedStatisticalDriftRule, ...] = ()
    required_sections: tuple[RequiredSection, ...] = ()
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
    # Per-rule settings, one map per stage: a rule listed here is off on that stage, or uses its own action there
    # instead of its check's action for that stage.
    # Each map may name only catalog rules and this record's own rules (checked in consistent), so it can never be larger
    # than those; there is no separate count limit (2026-11-05: the old cap, catalog + 100, would have refused a setting
    # for every comparison of a record with more than 100).
    input_rule_actions: RuleActions = Field(default_factory=dict)
    output_rule_actions: RuleActions = Field(default_factory=dict)
    # Independent Windows event rule settings; absent means Off. Never run on AI text.
    sigma_rule_actions: dict[str, Literal["allow", "flag", "block"]] = Field(default_factory=dict, max_length=4096)

    @model_validator(mode="before")
    @classmethod
    def renamed_check_fields(cls, data):
        """Records stored before 2026-11-04 hold one field per check (check_<id>) and one rule map (rule_actions),
        each for both stages. When such a record is read, check_<id> sets input_check_<id> and output_check_<id>
        and rule_actions sets both maps, so nothing changes until the tenant
        sets a stage differently. The API accepts only the new names.

        The fields of the two removed outside-provider checks (RETIRED_CHECK_IDS) are dropped here too. Every stored
        record holds them (null when off). One that is on can only be on a customer record (demo records could never
        turn them on); such a record is refused so it is reviewed and moved to harmful_content or prompt_attack, not
        silently changed. On the hosted site the two checks never ran: their provider was not configured."""
        if not isinstance(data, dict):
            return data
        retired = RETIRED_CHECK_FIELDS & data.keys()
        if retired:
            data = dict(data)
            on = sorted(name for name in retired if data[name] is not None)
            if on and data.get("demo") is not True:
                raise ValueError("content_safety and azure_prompt_shields were removed (2026-11-05): GuardOath runs "
                                 "every check itself. This customer record still turns one on and needs a migration "
                                 "review (use harmful_content and prompt_attack)")
            for name in retired:
                data.pop(name)
        if not (LEGACY_FIELDS & data.keys()):
            return data
        data = dict(data)
        for check_id in CHECK_IDS:
            if "check_" + check_id in data:
                value = data.pop("check_" + check_id)
                data["input_check_" + check_id] = value
                data["output_check_" + check_id] = value
        if "rule_actions" in data:
            value = data.pop("rule_actions")
            data["input_rule_actions"] = value
            data["output_rule_actions"] = value
        return data

    @model_validator(mode="before")
    @classmethod
    def retired_profile_field(cls, data):
        """Records stored before example profiles were removed may hold default_profile. "tenant" (the
        record's own checks) and any value on a demo record are dropped. A customer record that names an
        example profile is refused, so it is migrated by review instead of silently changing its checks."""
        if not isinstance(data, dict) or RETIRED_PROFILE_FIELD not in data:
            return data
        data = dict(data)
        value = data.pop(RETIRED_PROFILE_FIELD)
        if value != TENANT_POLICY_VERSION and data.get("demo") is not True:
            raise ValueError("default_profile: example profiles were removed; this customer record names one "
                             "and needs a migration review")
        return data

    @model_validator(mode="after")
    def consistent(self):
        # Duplicate comparison IDs would overwrite a rule in the tenant's rule table.
        # Reject them rather than silently hiding an active comparison.
        comparison_ids = [rule.rule_id for rule in (*self.comparisons, *self.advanced_arithmetic, *self.advanced_identity, *self.advanced_conversion_time, *self.advanced_trusted_reference, *self.advanced_statistical_drift)]
        if len(set(comparison_ids)) != len(comparison_ids):
            raise ValueError("Comparison rule IDs must be distinct")
        on = self.checks()
        if not on:
            raise ValueError("Turn on at least one check")
        for check_id, actions in on.items():
            if Action.REDACT in actions and check_id != "pii":
                raise ValueError(f"Only the pii check can redact; choose block, flag or allow for {check_id}")
        for check_id, names in REQUIRED_SETTINGS.items():
            if check_id in on and not any(getattr(self, name) for name in names):
                raise ValueError(f"The {check_id} check needs its settings before it can run: {', '.join(names)}")
        # The record's own comparisons and required sections are rules too (guardoath.record_rules); their ids may
        # not name a static rule, so a setting can never reach another check's rule.
        problems = bad_record_rule_ids(self, RULES)
        if problems:
            raise ValueError(problems[0])
        own = record_rule_ids(self)
        for stage in STAGES:
            actions = self.rule_actions_on(stage)
            # Messages never repeat submitted values, so an unknown rule id is not echoed back. A record may name
            # only static rules and its own comparisons and sections: never another tenant's.
            if any(rule_id not in RULES and rule_id not in own for rule_id in actions):
                raise ValueError("Unknown rule id; GET /v1/controls lists every rule, and this record's own "
                                 "comparisons and required sections are rules too")
            # Only rules of the pii check itself: encoded personal data (pii.encoded_*) never redacts.
            if self.action("pii", stage) == Action.REDACT and any(
                    rule_id in RULES and RULES[rule_id].control_id == "pii" and action != "off"
                    for rule_id, action in actions.items()):
                raise ValueError(f"When the pii check redacts on {stage.value}, its {stage.value} rules can only "
                                 "be turned off")
        if self.demo:
            if self.overage != "block":
                raise ValueError("Demo users have a hard monthly limit")
        if self.sigma_rule_actions:
            from guardoath.sigma_site import sigma_rule_ids
            if any(rule_id not in sigma_rule_ids() for rule_id in self.sigma_rule_actions):
                raise ValueError("Unknown Sigma event rule id")
        # Field-level rules of every stored setting, including settings of checks that are off.
        Policy(application_id=self.tenant_id, version=TENANT_POLICY_VERSION, rules=(),
               **{name: getattr(self, name) for name in SETTING_FIELDS})
        return self

    def action(self, check_id: str, stage: Stage | str) -> Action | None:
        """A check's action on one stage; None = it does not run there."""
        return getattr(self, check_field(check_id, Stage(stage)))

    def rule_actions_on(self, stage: Stage | str) -> dict[str, str]:
        """The per-rule settings of one stage."""
        return getattr(self, rule_actions_field(stage))

    def checks(self) -> dict[str, tuple[Action | None, Action | None]]:
        """The checks that run on at least one stage, as (input action, output action), in CHECK_IDS order."""
        both = {check_id: (self.action(check_id, Stage.INPUT), self.action(check_id, Stage.OUTPUT))
                for check_id in CHECK_IDS}
        return {check_id: actions for check_id, actions in both.items() if actions != (None, None)}

    def stages_of(self, check_id: str) -> tuple[Stage, ...]:
        """The stages a check runs on: the stages its settings cover (see the module docstring)."""
        if check_id == "schema":
            covered = {stage for stage, schema in ((Stage.INPUT, self.input_schema), (Stage.OUTPUT, self.output_schema))
                       if schema is not None}
        elif check_id == "cross_field":
            covered = {stage for comparison in (*self.comparisons, *self.advanced_arithmetic, *self.advanced_identity, *self.advanced_conversion_time, *self.advanced_trusted_reference, *self.advanced_statistical_drift)
                       for stage in comparison.stages}
        elif check_id == "completeness":
            covered = {stage for section in self.required_sections for stage in section.stages}
        else:
            return (Stage.INPUT, Stage.OUTPUT)
        # The record's own validation (REQUIRED_SETTINGS) refuses an on check with no settings at all.
        return tuple(stage for stage in (Stage.INPUT, Stage.OUTPUT) if stage in covered) or (Stage.INPUT, Stage.OUTPUT)

    def to_policy(self) -> Policy:
        rules = []
        for check_id, (input_action, output_action) in self.checks().items():
            # A check runs on the stages its settings cover (stages_of) where it has an action.
            stages = tuple(stage for stage in self.stages_of(check_id)
                           if (input_action if stage == Stage.INPUT else output_action) is not None)
            if stages:
                rules.append(Rule(checker_id=check_id, on_finding=input_action or output_action,
                                  on_input_finding=input_action, on_output_finding=output_action, stages=stages))
        ran = {rule.checker_id for rule in rules}
        settings = {name: getattr(self, name) for check_id, names in SETTINGS_OF.items()
                    if check_id in ran for name in names}
        # Rule settings go with their check; the pii ones also apply to encoded_pii, which reuses the pii scan.
        running = ran | ({"pii"} if "encoded_pii" in ran else set())
        own = record_rule_ids(self)

        def kept(actions):
            return {rule_id: action for rule_id, action in actions.items()
                    if (RULES[rule_id].control_id if rule_id in RULES else own[rule_id]) in running}
        return Policy(application_id=self.tenant_id, version=TENANT_POLICY_VERSION, mode=self.mode,
                      timeout_ms=self.timeout_ms, failure_action=self.failure_action, rules=tuple(rules),
                      input_rule_actions=kept(self.input_rule_actions),
                      output_rule_actions=kept(self.output_rule_actions), **settings)

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


def record_bytes(record: TenantRecord) -> int:
    """The size of the record's stored fields, exactly as guardoath.dynamodb_store.record_json writes them."""
    return len(json.dumps(record.model_dump(mode="json", exclude={"usage", "revision"}), separators=(",", ":"),
                          sort_keys=True).encode("utf-8"))
