"""Strict contracts shared by the API, policy engine, and adapters."""

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from guardoath.privacy_catalog import DEFAULT_MIN_SCORE, PATTERN_ENTITIES


# Finding rule ids, such as "pii.iban_code" or "agent.git_force".
RULE_ID_PATTERN = r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Action(StrEnum):
    ALLOW = "allow"
    FLAG = "flag"
    REDACT = "redact"
    BLOCK = "block"


class Stage(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


class CheckRequest(Contract):
    application_id: str = Field(min_length=1, max_length=100)
    policy_version: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=32_000)
    language: Literal["en"] = "en"
    # One canonical content value is used by every checker.
    content_format: Literal["text", "json"] = "text"
    request_context: str | None = Field(default=None, max_length=8_000)
    validation_codes: tuple[Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{1,31}$", strict=True)], ...] = Field(default=(), max_length=12)


class ApiCheckRequest(CheckRequest):
    """A check request sent to the HTTP API. With a tenant key, policy_version may be left out: the
    tenant record's default profile is used. Every other key must name a profile."""
    policy_version: str | None = Field(default=None, min_length=1, max_length=100)


class Finding(Contract):
    rule_id: str
    category: str
    severity: Literal["low", "medium", "high"]
    explanation: str
    # Never include source text or provider exception messages in findings.


class CheckerResult(Contract):
    checker_id: str
    checker_version: str
    status: Literal["passed", "flagged", "error", "skipped", "unsupported"]
    findings: tuple[Finding, ...] = ()
    latency_ms: float = 0
    error_code: str | None = None
    simulated: bool = False
    # Private intermediate value: never serialized as a checker finding/result.
    transformed_content: str | None = Field(default=None, exclude=True)


class Rule(Contract):
    checker_id: str
    required: bool = True
    on_finding: Action = Action.FLAG
    stages: tuple[Stage, ...] = Field(default=(Stage.INPUT, Stage.OUTPUT), min_length=1)
    on_input_finding: Action | None = None
    on_output_finding: Action | None = None


class FieldComparison(Contract):
    rule_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    left: str = Field(min_length=1, max_length=200)
    right: str = Field(min_length=1, max_length=200)
    operator: Literal["lt", "le", "eq", "ge", "gt", "ne"]
    value_type: Literal["number", "date"] = "number"
    stages: tuple[Stage, ...] = Field(default=(Stage.INPUT, Stage.OUTPUT), min_length=1)

    @field_validator("left", "right")
    @classmethod
    def pointer(cls, value):
        import re
        if not value.startswith("/") or re.search(r"~(?![01])", value):
            raise ValueError("Expected an absolute JSON Pointer")
        return value


# FC-031/032/033/034/036/037/041/046: tenant-owned exact arithmetic.
# Each kind names precisely the fields it reads. A pointer describes an input
# location, not an expression or code to execute.
ADVANCED_FIELD_KEYS = {
    "invoice_line_subtotal": ("lines", "subtotal"),
    "invoice_total": ("subtotal", "discount", "tax", "shipping", "total"),
    "tax_tolerance": ("tax", "base", "rate", "tolerance"),
    "discount_fraction_cap": ("discount", "eligible_subtotal", "max_fraction"),
    "inventory_conservation": ("opening", "received", "shipped", "lost", "closing"),
    "shipment_quantity_sum": ("lines", "total"),
    "manufacturing_tolerance": ("measured", "nominal", "tolerance"),
    "carbon_intensity": ("emissions", "output", "max_intensity"),
}


class AdvancedArithmeticRule(Contract):
    rule_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    kind: Literal["invoice_line_subtotal", "invoice_total", "tax_tolerance",
                  "discount_fraction_cap", "inventory_conservation",
                  "shipment_quantity_sum", "manufacturing_tolerance", "carbon_intensity"]
    fields: dict[str, str] = Field(min_length=2, max_length=5)
    stages: tuple[Stage, ...] = Field(default=(Stage.INPUT, Stage.OUTPUT), min_length=1)

    @model_validator(mode="after")
    def valid_fields(self):
        import re
        if set(self.fields) != set(ADVANCED_FIELD_KEYS[self.kind]):
            raise ValueError("Arithmetic rule requires its declared field names")
        decoded = []
        for pointer in self.fields.values():
            if not isinstance(pointer, str) or len(pointer) > 200 or not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
                raise ValueError("Arithmetic fields require absolute JSON Pointers")
            segments = tuple(x.replace("~1", "/").replace("~0", "~") for x in pointer[1:].split("/"))
            if any(not x or x.isdigit() for x in segments):
                raise ValueError("Arithmetic pointers require object fields, not positional indices")
            decoded.append(segments)
        if len(set(decoded)) != len(decoded) or any(
                p == q[:len(p)] or q == p[:len(q)]
                for i, p in enumerate(decoded) for q in decoded[i + 1:]):
            raise ValueError("Arithmetic fields must be distinct and nonoverlapping")
        if len(set(self.stages)) != len(self.stages):
            raise ValueError("Arithmetic stages must be distinct")
        return self


# FC-035/042/043/050: tenant-owned conversion and temporal controls.
# Currency rates are versioned tenant-saved references, never request-supplied authority.
CONVERSION_TIME_FIELD_KEYS = {
    "currency_conversion_cap": ("payment_amount", "payment_currency", "invoice_amount", "invoice_currency"),
    "unit_load_kg_cap": ("load", "load_unit", "max_kg"),
    "cold_chain_excursion": ("excursion_start", "excursion_end", "allowed_minutes"),
    "lifecycle_chronology": ("order_at", "shipment_at", "delivery_at", "return_at"),
}


class AdvancedConversionTimeRule(Contract):
    rule_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    kind: Literal["currency_conversion_cap", "unit_load_kg_cap",
                  "cold_chain_excursion", "lifecycle_chronology"]
    fields: dict[str, str] = Field(min_length=3, max_length=4)
    stages: tuple[Stage, ...] = Field(default=(Stage.INPUT, Stage.OUTPUT), min_length=1)
    # FX reference is a bounded, tenant-saved, versioned ratio. This asserts
    # tenancy of the setting, not external attestation of a market exchange rate.
    source_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    destination_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    trusted_rate_numerator: int | None = Field(default=None, gt=0, le=1_000_000_000, strict=True)
    trusted_rate_denominator: int | None = Field(default=None, gt=0, le=1_000_000_000, strict=True)
    rate_reference: str | None = Field(default=None, min_length=1, max_length=100)
    rate_asof: str | None = Field(default=None, min_length=20, max_length=35)

    @model_validator(mode="after")
    def validate_conversion_time(self):
        import re
        from datetime import datetime
        if set(self.fields) != set(CONVERSION_TIME_FIELD_KEYS[self.kind]):
            raise ValueError("Conversion/time rule requires its declared field names")
        decoded = []
        for pointer in self.fields.values():
            if (not isinstance(pointer, str) or len(pointer) > 200 or
                    not pointer.startswith("/") or re.search(r"~(?![01])", pointer)):
                raise ValueError("Conversion/time fields require absolute JSON Pointers")
            segments = tuple(x.replace("~1", "/").replace("~0", "~") for x in pointer[1:].split("/"))
            if any(not x or x.isdigit() for x in segments):
                raise ValueError("Conversion/time pointers require object fields")
            decoded.append(segments)
        if len(set(decoded)) != len(decoded) or any(
                a == b[:len(a)] or b == a[:len(b)]
                for i, a in enumerate(decoded) for b in decoded[i + 1:]):
            raise ValueError("Conversion/time pointers must not overlap")
        if len(set(self.stages)) != len(self.stages):
            raise ValueError("Conversion/time stages must be distinct")
        reference = (self.source_currency, self.destination_currency,
                     self.trusted_rate_numerator, self.trusted_rate_denominator,
                     self.rate_reference, self.rate_asof)
        if self.kind == "currency_conversion_cap":
            if any(value is None for value in reference):
                raise ValueError("Currency conversion requires versioned tenant-owned FX reference")
            if self.source_currency == self.destination_currency:
                raise ValueError("Currency pair must be distinct")
            if not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,100}", self.rate_reference):
                raise ValueError("FX reference identifier is invalid")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])", self.rate_asof):
                raise ValueError("FX reference timestamp must be explicit-offset ISO-8601")
            try:
                asof = datetime.fromisoformat(self.rate_asof.replace("Z", "+00:00"))
                if asof.utcoffset() is None:
                    raise ValueError("Timezone is required")
            except (ValueError, OverflowError) as exc:
                raise ValueError("FX reference timestamp is invalid") from exc
        elif any(value is not None for value in reference):
            raise ValueError("Only currency conversion may carry an FX reference")
        return self


IDENTITY_FIELD_KEYS = {
    "order_id_match": ("order_id", "shipment_order_id"),
    "quarantine_exclusion": ("requested_lot",),
    "serial_unique": ("serials",),
    "authorized_scope_subset": ("requested_scopes",),
}


class AdvancedIdentityRule(Contract):
    """FC-038/039/040/048; reference sets come from trusted tenant configuration."""
    rule_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    kind: Literal["order_id_match", "quarantine_exclusion",
                  "serial_unique", "authorized_scope_subset"]
    fields: dict[str, str] = Field(min_length=1, max_length=2)
    trusted_values: tuple[str, ...] = Field(default=(), max_length=128)
    stages: tuple[Stage, ...] = Field(default=(Stage.INPUT, Stage.OUTPUT), min_length=1)

    @model_validator(mode="after")
    def valid_identity_rule(self):
        import re
        if set(self.fields) != set(IDENTITY_FIELD_KEYS[self.kind]):
            raise ValueError("Identity rule requires its declared field names")
        decoded = []
        for pointer in self.fields.values():
            if not isinstance(pointer, str) or len(pointer) > 200 or not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
                raise ValueError("Identity fields require absolute JSON Pointers")
            segments = tuple(x.replace("~1", "/").replace("~0", "~") for x in pointer[1:].split("/"))
            if any(not x or x.isdigit() for x in segments):
                raise ValueError("Identity fields require object fields, not positional indices")
            decoded.append(segments)
        if len(set(decoded)) != len(decoded) or any(
                p == q[:len(p)] or q == p[:len(q)]
                for i, p in enumerate(decoded) for q in decoded[i + 1:]):
            raise ValueError("Identity pointers must be distinct and nonoverlapping")
        referenced = self.kind in ("quarantine_exclusion", "authorized_scope_subset")
        if referenced != bool(self.trusted_values):
            raise ValueError("Trusted reference values required only for reference-backed identity rules")
        if len(set(self.trusted_values)) != len(self.trusted_values):
            raise ValueError("Trusted reference values must be distinct")
        if any(not isinstance(value, str) or
               not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", value)
               for value in self.trusted_values):
            raise ValueError("Invalid trusted reference identifier")
        if len(set(self.stages)) != len(self.stages):
            raise ValueError("Identity stages must be distinct")
        return self


TRUSTED_REFERENCE_FIELDS = {
    "dose_ceiling": ("dose_mg",),
    "medication_interval": ("administered_at",),
    "permit_cap": ("reported_emission",),
    "invoice_po_vendor": ("invoice_vendor_id",),
}
TRUSTED_REFERENCE_VALUES = {
    "dose_ceiling": ("verified_weight_kg", "max_mg_per_kg"),
    "medication_interval": ("previous_administration", "minimum_interval_minutes"),
    "permit_cap": ("permit_limit",),
    "invoice_po_vendor": ("approved_vendor_id",),
}


class AdvancedTrustedReferenceRule(Contract):
    """FC-044/045/047/049: only host-signed tenant-bound snapshots are authoritative."""
    rule_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    kind: Literal["dose_ceiling", "medication_interval", "permit_cap", "invoice_po_vendor"]
    fields: dict[str, str] = Field(min_length=1, max_length=1)
    reference_values: dict[str, str] = Field(min_length=1, max_length=2)
    source_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{3,100}$")
    reference_version: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,80}$")
    verified_at: str = Field(min_length=20, max_length=40)
    expires_at: str = Field(min_length=20, max_length=40)
    host_signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    stages: tuple[Stage, ...] = Field(default=(Stage.INPUT, Stage.OUTPUT), min_length=1)

    @model_validator(mode="after")
    def trusted_contract(self):
        import re
        if set(self.fields) != set(TRUSTED_REFERENCE_FIELDS[self.kind]) or set(self.reference_values) != set(TRUSTED_REFERENCE_VALUES[self.kind]):
            raise ValueError("Trusted reference rule requires its exact declared field names")
        for pointer in self.fields.values():
            if not isinstance(pointer, str) or len(pointer) > 200 or not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
                raise ValueError("Trusted reference requires absolute JSON Pointers")
        if any(not isinstance(v, str) or not 1 <= len(v) <= 100 for v in self.reference_values.values()):
            raise ValueError("Invalid trusted reference value")
        if len(set(self.stages)) != len(self.stages):
            raise ValueError("Trusted reference stages must be distinct")
        return self


class AdvancedStatisticalDriftRule(Contract):
    """Tenant-owned batch PSI, not a general streaming drift monitor.

    Reference histogram and threshold are tenant configuration, never supplied
    by the incoming JSON. Stage/action policies remain authoritative.
    """
    rule_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    observations_pointer: str = Field(min_length=2, max_length=200)
    reference_id: str = Field(min_length=1, max_length=100)
    reference_version: str = Field(min_length=1, max_length=80)
    bin_edges: tuple[str, ...] = Field(min_length=3, max_length=65)
    counts: tuple[Annotated[int, Field(strict=True, ge=0)], ...] = Field(min_length=2, max_length=64)
    psi_threshold: str = Field(min_length=1, max_length=24)
    min_observations: int = Field(default=30, ge=30, le=100_000, strict=True)
    stages: tuple[Stage, ...] = Field(default=(Stage.INPUT, Stage.OUTPUT), min_length=1)

    @model_validator(mode="after")
    def valid_drift_rule(self):
        import re
        from decimal import Decimal, InvalidOperation
        from guardoath.statistical_drift import ReferenceHistogram, _validate_reference
        if (not self.observations_pointer.startswith("/")
                or re.search(r"~(?![01])", self.observations_pointer)
                or any(not x for x in self.observations_pointer[1:].split("/"))):
            raise ValueError("Drift observations require a bounded JSON pointer")
        if len(set(self.stages)) != len(self.stages):
            raise ValueError("Drift stage names must be unique")
        _validate_reference(ReferenceHistogram(
            self.reference_id, self.reference_version, self.bin_edges, self.counts))
        if sum(self.counts) < self.min_observations:
            raise ValueError("Drift reference must satisfy minimum sample count")
        try:
            threshold = Decimal(self.psi_threshold)
        except InvalidOperation:
            raise ValueError("Invalid PSI threshold") from None
        if not threshold.is_finite() or threshold <= 0 or threshold > 10:
            raise ValueError("PSI threshold must be finite, positive, <=10")
        return self


class RequiredSection(Contract):
    rule_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    label: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9 _-]{0,79}$")
    min_characters: int = Field(default=1, ge=1, le=8_000)
    stages: tuple[Stage, ...] = Field(default=(Stage.INPUT, Stage.OUTPUT), min_length=1)

    @field_validator("label")
    @classmethod
    def trimmed_label(cls, value):
        if value != value.strip():
            raise ValueError("Section labels must not have surrounding whitespace")
        return value


class ConfidentialTerm(Contract):
    rule_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    term: str = Field(min_length=3,max_length=200)
    stages: tuple[Stage, ...] = Field(default=(Stage.INPUT,Stage.OUTPUT),min_length=1)

    @field_validator('term')
    @classmethod
    def meaningful(cls,value):
        if value != value.strip() or len(value.strip()) < 3:
            raise ValueError('Confidential terms require at least three non-padding characters')
        return value


class MatchedOutcomes(Contract):
    attributes: tuple[Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,49}$", strict=True)], ...] = Field(min_length=1, max_length=8)
    outcome_type: Literal["string", "boolean", "number"] = "string"

    @field_validator("attributes")
    @classmethod
    def unique_attributes(cls, value):
        if len({name.casefold() for name in value}) != len(value):
            raise ValueError("Matched attributes must be distinct")
        return value


class EvidenceSource(Contract):
    source_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$", strict=True)
    text: str = Field(min_length=1, max_length=8_000, strict=True)

    @field_validator("text")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("A configured evidence source must not be blank")
        return value


class EvidencePolicy(Contract):
    sources: tuple[EvidenceSource, ...] = Field(min_length=1, max_length=20)
    min_quote_characters: int = Field(default=20, ge=1, le=1_000, strict=True)

    @field_validator("sources")
    @classmethod
    def unique_sources(cls, value):
        if len({source.source_id for source in value}) != len(value):
            raise ValueError("Evidence source identifiers must be distinct")
        return value


class ToolDefinition(Contract):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,49}$", strict=True)
    arguments_schema: dict[str, Any]


class ToolPolicy(Contract):
    tools: tuple[ToolDefinition, ...] = Field(min_length=1, max_length=20)

    @field_validator("tools")
    @classmethod
    def unique_tools(cls, value):
        if len({tool.name for tool in value}) != len(value):
            raise ValueError("Configured tool names must be distinct")
        return value


class LedgerPolicy(Contract):
    currency: str = Field(pattern=r"^[A-Z]{3}$", min_length=3, max_length=3, strict=True)
    decimal_places: int = Field(default=2, ge=0, le=4, strict=True)
    max_entries: int = Field(default=100, ge=2, le=200, strict=True)


class TimestampPolicy(Contract):
    allow_equal: bool = Field(default=False, strict=True)
    max_samples: int = Field(default=200, ge=2, le=500, strict=True)


class GraphPolicy(Contract):
    max_nodes: int = Field(default=100, ge=1, le=200, strict=True)
    max_edges: int = Field(default=500, ge=1, le=1_000, strict=True)


class IntegrityPolicy(Contract):
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$", min_length=64, max_length=64, strict=True)
    encoding: Literal["utf8", "base64"] = "utf8"
    max_bytes: int = Field(default=16_384, ge=1, le=262_144, strict=True)

    @field_validator("sha256")
    @classmethod
    def normalized_digest(cls, value):
        return value.lower()


class Policy(Contract):
    application_id: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    mode: Literal["observe", "enforce"] = "observe"
    rules: tuple[Rule, ...]
    timeout_ms: int = Field(default=250, ge=1, le=5_000)
    failure_action: Literal["block", "flag"] = "block"
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    allow_simulated_enforcement: bool = False
    # No count limit (2026-11-05): a tenant record's size is bounded (tenant_record.MAX_RECORD_BYTES), and policy files are
    # trusted configuration.
    comparisons: tuple[FieldComparison, ...] = ()
    advanced_arithmetic: tuple[AdvancedArithmeticRule, ...] = ()
    advanced_identity: tuple[AdvancedIdentityRule, ...] = ()
    advanced_conversion_time: tuple[AdvancedConversionTimeRule, ...] = ()
    advanced_trusted_reference: tuple[AdvancedTrustedReferenceRule, ...] = ()
    advanced_statistical_drift: tuple[AdvancedStatisticalDriftRule, ...] = ()
    required_sections: tuple[RequiredSection, ...] = ()

    @model_validator(mode="after")
    def all_comparison_rule_ids_are_unique(self):
        ids = [r.rule_id for r in (*self.comparisons, *self.advanced_arithmetic, *self.advanced_identity, *self.advanced_conversion_time, *self.advanced_trusted_reference, *self.advanced_statistical_drift)]
        if len(set(ids)) != len(ids):
            raise ValueError("Comparison rule IDs must be distinct")
        return self
    detect_labeled_sensitive_data: bool = Field(default=False, strict=True)
    # Copied privacy pattern checks run with every pii rule unless turned off here.
    privacy_patterns: bool = Field(default=True, strict=True)
    disabled_privacy_entities: tuple[Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$", strict=True)], ...] = Field(default=(), max_length=100)
    privacy_min_score: float = Field(default=DEFAULT_MIN_SCORE, ge=0.0, le=1.0, allow_inf_nan=False)
    detect_secret_assignments: bool = Field(default=False,strict=True)
    inspect_encoded_secrets: bool = Field(default=False,strict=True)
    confidential_terms: tuple[ConfidentialTerm, ...] = Field(default=(),max_length=50)
    matched_outcomes: MatchedOutcomes | None = None
    evidence: EvidencePolicy | None = None
    tool_calls: ToolPolicy | None = None
    ledger: LedgerPolicy | None = None
    timestamps: TimestampPolicy | None = None
    graph: GraphPolicy | None = None
    integrity: IntegrityPolicy | None = None
    # Per-rule settings (from a tenant record's rule_actions): "off" drops that rule's findings; any other
    # value replaces the check's action for that rule's findings. The record checks ids against the catalog, so a map
    # is never larger than the catalog plus the record's own rules (no separate count limit since 2026-11-05: the
    # catalog is growing past 1,000 rules).
    rule_actions: dict[Annotated[str, Field(pattern=RULE_ID_PATTERN, max_length=100)],
                       Literal["off", "allow", "flag", "block"]] = Field(default_factory=dict)
    # Per-stage rule settings (tenant records, 2026-11-04). When set, a stage uses its own map instead of rule_actions.
    input_rule_actions: dict[Annotated[str, Field(pattern=RULE_ID_PATTERN, max_length=100)],
                             Literal["off", "allow", "flag", "block"]] | None = None
    output_rule_actions: dict[Annotated[str, Field(pattern=RULE_ID_PATTERN, max_length=100)],
                              Literal["off", "allow", "flag", "block"]] | None = None

    def rule_actions_for(self, stage: "Stage") -> dict[str, str]:
        """The rule settings that apply on one stage."""
        own = self.input_rule_actions if stage == Stage.INPUT else self.output_rule_actions
        return self.rule_actions if own is None else own

    @field_validator("privacy_min_score", mode="before")
    @classmethod
    def numeric_score(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("privacy_min_score must be a number")
        return value

    @field_validator("disabled_privacy_entities")
    @classmethod
    def known_privacy_entities(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Disabled privacy entity types must be distinct")
        if any(entity not in PATTERN_ENTITIES for entity in value):
            raise ValueError("Unknown privacy pattern entity type")
        return value

    @model_validator(mode="after")
    def consistent_privacy_patterns(self):
        if not self.privacy_patterns and self.disabled_privacy_entities:
            raise ValueError("disabled_privacy_entities has no effect when privacy_patterns is false")
        return self

    @model_validator(mode="after")
    def rule_actions_with_redaction(self):
        # Redaction replaces every value the pii check finds; a pii rule can then only be turned off on the stages
        # where pii redacts. pii.encoded_* rules belong to the encoded personal data check, which never redacts.
        for stage in Stage:
            redacts = any(((rule.on_input_finding if stage == Stage.INPUT else rule.on_output_finding)
                           or rule.on_finding) == Action.REDACT
                          for rule in self.rules if rule.checker_id == "pii" and stage in rule.stages)
            if redacts and any(rule_id.startswith("pii.") and not rule_id.startswith("pii.encoded_") and action != "off"
                               for rule_id, action in self.rule_actions_for(stage).items()):
                raise ValueError("When the pii check redacts, its rules can only be turned off")
        return self


class GroupResult(Contract):
    group_id: str
    name: str
    status: Literal["passed", "findings", "incomplete", "simulated", "partial", "disabled", "not_applicable", "not_implemented"]
    checker_ids: tuple[str, ...] = ()
    limitations: str


class RedactionResult(Contract):
    status: Literal["not_requested", "not_needed", "preview", "ready", "withheld"] = "not_requested"
    content: str | None = None
    explanation: str = "No redaction was requested by the policy."
    validation_results: tuple[CheckerResult, ...] = ()


class CheckResponse(Contract):
    check_id: str
    application_id: str
    policy_version: str
    applied_validation_packs: tuple[str, ...] = ()
    stage: Stage
    mode: str
    evaluation_status: Literal["complete", "incomplete"]
    recommended_action: Action
    effective_action: Action
    findings: tuple[Finding, ...]
    checker_results: tuple[CheckerResult, ...]
    total_latency_ms: float
    # Every check runs in GuardOath itself, so a check has no per-call provider cost (2026-11-05).
    cost_usd: float = 0
    cost_basis: Literal["local_only"] = "local_only"
    simulated: bool
    groups: tuple[GroupResult, ...] = ()
    redaction: RedactionResult = Field(default_factory=RedactionResult)
