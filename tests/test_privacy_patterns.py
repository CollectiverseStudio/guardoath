"""Copied privacy pattern checks: on by default, configurable per policy, bounded."""
import asyncio
import random

import pytest
from pydantic import ValidationError

pytest.importorskip("tldextract")
pytest.importorskip("phonenumbers")

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, Policy, Rule, Stage
from guardoath.privacy_catalog import CORE_ENTITIES, PATTERN_ENTITIES

# One example per pattern type, taken from the copied upstream tests (synthetic values).
EXAMPLES = {
    "ABA_ROUTING_NUMBER": "121000358",
    "AU_ABN": "51824753556",
    "AU_ACN": "006249976",
    "AU_MEDICARE": "2123 45670 1",
    "AU_TFN": "876543210",
    "CA_POSTAL_CODE": "Ontario K1A 0A1",
    "CA_SIN": "130 692 544",
    "CRYPTO": "16Yeky6GMjeNkAiNcBY7ZhrLoMSgg1BoyZ",
    "DATE_TIME": "5-20-2021",
    "DE_BSNR": "BSNR 711234567 der Praxis.",
    "DE_FUEHRERSCHEIN": "Führerscheinnummer: BO12345678A",
    "DE_HANDELSREGISTER": "HRB 1",
    "DE_HEALTH_INSURANCE": "A000500015",
    "DE_ID_CARD": "L01X00T44",
    "DE_KFZ": "Kennzeichen: HH-AB-1234.",
    "DE_LANR": "234567701",
    "DE_PASSPORT": "C01234565",
    "DE_PLZ": "PLZ 80331",
    "DE_SOCIAL_SECURITY": "15070649C103",
    "DE_TAX_ID": "98765432106",
    "DE_TAX_NUMBER": "987/654/32100",
    "DE_VAT_ID": "DE136695976",
    "ES_NIE": "Z8078221M",
    "ES_NIF": "1111111G",
    "ES_PASSPORT": "Mi pasaporte es AAA123456",
    "FI_PERSONAL_IDENTITY_CODE": "131052-308T",
    "IBAN_CODE": "IBAN DE89 3704 0044 0532 0130 00",
    "IN_AADHAAR": "312345678909",
    "IN_GSTIN": "27ABCDE1234F1Z5",
    "IN_PAN": "ABCPD1234Z",
    "IN_PASSPORT": "passport number: J6932157",
    "IN_VEHICLE_REGISTRATION": "KA53ME3456",
    "IN_VOTER": "KSD1287349",
    "IP_ADDRESS": "fe80::1",
    "IT_DRIVER_LICENSE": "patente: AA0123456B",
    "IT_FISCAL_CODE": "AAAAAA00B11C333Y",
    "IT_IDENTITY_CARD": "carta: 1234567Aa",
    "IT_PASSPORT": "passaporto: AA1234567",
    "IT_VAT_CODE": "01333550_323",
    "KR_BRN": "104-86-56659",
    "KR_DRIVER_LICENSE": "11-22-123456-12",
    "KR_FRN": "911124-5678901",
    "KR_PASSPORT": "대한민국 여권 S87654321",
    "KR_RRN": "960121-1234567",
    "MAC_ADDRESS": "MAC: 01:23:45:67:89:AB",
    "MEDICAL_LICENSE": "DEA number AB1234563",
    "NG_NIN": "Nigerian NIN 23456789019",
    "NG_VEHICLE_REGISTRATION": "APP456CV",
    "PHONE_NUMBER": "_: (415)555-0132",
    "PH_PASSPORT": "My Philippine passport number is P1234567A.",
    "PH_TIN": "TIN: 000123456",
    "PH_UMID": "0111-1234567-8",
    "PL_PESEL": "44051401458",
    "SE_ORGANISATIONSNUMMER": "5567037485",
    "SE_PERSONNUMMER": "20110925-2385",
    "SG_NRIC_FIN": "S2740116C",
    "SG_UEN": "53125226D",
    "TH_TNIN": "2345678901234",
    "TR_LICENSE_PLATE": "01 A 12",
    "TR_NATIONAL_ID": "10000000146",
    "UK_DRIVING_LICENCE": "MORGA607054SM9IJ",
    "UK_NHS": "401-023-2137",
    "UK_NINO": "tw987654a",
    "UK_PASSPORT": "Passports: AB1234567 and XY9876543",
    "UK_POSTCODE": "My address is SW1A 1AA in London",
    "UK_VEHICLE_REGISTRATION": "BD62XYZ",
    "URL": "microsoft.com",
    "US_BANK_NUMBER": "check: 945456787654",
    "US_CLAIM_NUMBER": "cLaIm clm123456",
    "US_DRIVER_LICENSE": "driver: H12234567",
    "US_HEALTH_INSURANCE_MEMBER_ID": "Member ID A12345",
    "US_ITIN": "911-70-1234",
    "US_MBI": "1EG4-TE5-MK73",
    "US_NPI": "1234-567-893",
    # Upstream's example uses A12803456. A, D, M or T plus 8 digits is also a validated South
    # African passport number, which outranks the weak U.S. pattern on the same characters
    # (see test_privacy_overlaps.py), so this example starts with C.
    "US_PASSPORT": "my travel document is C12803456",
    "US_PRESCRIPTION_NUMBER": "Rx #1234567",
    "US_PRIOR_AUTHORIZATION_NUMBER": "pRiOr AuThOrIzAtIoN pa-123456",
    "US_PROVIDER_TAX_ID": "Provider EIN 12-3456789.",
    "US_REFERRAL_NUMBER": "rEfErRaL inf123456",
    "UUID": "6ba7b810-9dad-31d1-80b4-00c04fd430c8",
    "ZA_COMPANY_REGISTRATION": "CK2001/123456",
    "ZA_DRIVER_LICENSE": "60390002CGBV",
    "ZA_ID_NUMBER": "8001015009087",
    "ZA_INCOME_TAX_NUMBER": "1234567890",
    "ZA_LICENSE_PLATE": "KD93GKGP",
    "ZA_MOBILE_NUMBER": "+27632118258",
    "ZA_PASSPORT": "A34855903",
    "ZA_TELEPHONE_NUMBER": "011 262 5500",
    "ZA_TRAFFIC_REGISTER_NUMBER": "eNaTIS TRN 6001015000076 recorded.",
    "ZA_VAT_NUMBER": "SARS vat registration 4100168758",
}

MIXED = "Contact jane@example.com, IBAN DE89 3704 0044 0532 0130 00, phone (415) 555-0132."


@pytest.fixture(scope="module")
def checkers():
    return default_checkers(enable_pii=True)


def check(checkers, content, *, fmt="text", action=Action.FLAG, timeout_ms=500, **settings):
    policy = Policy(application_id="demo", version="v1", timeout_ms=timeout_ms,
                    rules=(Rule(checker_id="pii", on_finding=action),), **settings)
    request = CheckRequest(application_id="demo", policy_version="v1", content=content, content_format=fmt)
    return asyncio.run(Engine([policy], checkers).check(request, Stage.INPUT))


def rule_ids(result):
    return {f.rule_id for f in result.findings}


def test_catalog_matches_the_copied_recognizers():
    from guardoath.privacy_patterns import RECOGNIZER_CLASSES
    entities = {cls().supported_entities[0] for cls in RECOGNIZER_CLASSES}
    assert entities == set(PATTERN_ENTITIES) | set(CORE_ENTITIES)
    assert len(PATTERN_ENTITIES) == 90 and set(EXAMPLES) == set(PATTERN_ENTITIES)
    assert {severity for _, severity in PATTERN_ENTITIES.values()} <= {"low", "medium", "high"}


@pytest.mark.parametrize("entity", sorted(PATTERN_ENTITIES))
def test_every_pattern_type_is_detected_by_default(checkers, entity):
    result = check(checkers, EXAMPLES[entity])
    assert "pii." + entity.lower() in rule_ids(result)
    assert result.evaluation_status == "complete" and result.recommended_action == "flag"
    assert EXAMPLES[entity] not in result.model_dump_json()
    finding = next(f for f in result.findings if f.rule_id == "pii." + entity.lower())
    assert finding.severity == PATTERN_ENTITIES[entity][1] or entity == "PHONE_NUMBER"


def test_turning_pattern_checks_off_restores_email_card_and_ssn_only(checkers):
    # The domain inside the email address is not reported again as a URL.
    assert rule_ids(check(checkers, MIXED)) == {"pii.email_address", "pii.iban_code", "pii.phone_number"}
    assert rule_ids(check(checkers, MIXED, privacy_patterns=False)) == {"pii.email_address"}


def test_disabled_entity_types_are_not_reported(checkers):
    result = check(checkers, MIXED, disabled_privacy_entities=("IBAN_CODE", "URL"))
    assert rule_ids(result) == {"pii.email_address", "pii.phone_number"}


@pytest.mark.parametrize("settings", [
    {"disabled_privacy_entities": ("NOT_A_TYPE",)},
    {"disabled_privacy_entities": ("EMAIL_ADDRESS",)},
    {"disabled_privacy_entities": ("URL", "URL")},
    {"disabled_privacy_entities": ("url",)},
    {"privacy_patterns": False, "disabled_privacy_entities": ("URL",)},
    {"privacy_patterns": "no"},
    {"privacy_min_score": 1.5},
    {"privacy_min_score": -0.1},
    {"privacy_min_score": True},
    {"privacy_min_score": "0.4"},
])
def test_invalid_privacy_settings_are_configuration_errors(settings):
    with pytest.raises(ValidationError):
        Policy(application_id="demo", version="v1", rules=(Rule(checker_id="pii"),), **settings)


@pytest.mark.parametrize("settings", [{"privacy_patterns": False}, {"privacy_min_score": 0.5},
                                      {"disabled_privacy_entities": ("URL",)}])
def test_privacy_settings_require_a_pii_rule(checkers, settings):
    policy = Policy(application_id="demo", version="v1", rules=(Rule(checker_id="schema", required=False),), **settings)
    with pytest.raises(ValueError, match="pii checker rule"):
        Engine([policy], checkers)


def test_weak_patterns_count_only_with_context_at_the_default_score(checkers):
    assert not check(checkers, "Order 912803456 shipped.").findings
    assert "pii.us_passport" in rule_ids(check(checkers, "My passport number is 912803456."))
    # With a low minimum the weak matches count without context. Four pattern types match the
    # same nine digits; the highest score (U.S. ITIN, 0.3) is reported once for them.
    assert rule_ids(check(checkers, "Order 912803456 shipped.", privacy_min_score=0.05)) == {"pii.us_itin"}
    assert not check(checkers, "My passport number is 912803456.", privacy_min_score=1.0).findings
    assert rule_ids(check(checkers, "IBAN DE89 3704 0044 0532 0130 00", privacy_min_score=1.0)) == {"pii.iban_code"}


def test_json_field_names_count_as_context(checkers):
    assert rule_ids(check(checkers, '{"passport_number": "912803456"}', fmt="json")) == {"pii.us_passport"}
    assert not check(checkers, '{"order": "912803456"}', fmt="json").findings


def test_pattern_types_are_redacted_and_rechecked(checkers):
    result = check(checkers, "Send it to IBAN DE89 3704 0044 0532 0130 00 please.", action=Action.REDACT)
    assert result.redaction.status == "preview"
    assert result.redaction.content == "Send it to IBAN [REDACTED_IBAN_CODE] please."
    assert all(c.status == "passed" for c in result.redaction.validation_results)


def test_redaction_keeps_the_outer_label_for_contained_values(checkers):
    # The domain inside an email address also matches the URL pattern. It is reported once,
    # as the email address, which keeps its own label in the redacted text.
    result = check(checkers, "Email jane@example.com", action=Action.REDACT)
    assert rule_ids(result) == {"pii.email_address"}
    assert result.redaction.content == "Email [REDACTED_EMAIL_ADDRESS]"


def test_identical_spans_of_two_pattern_types_are_labeled_sensitive(checkers):
    result = check(checkers, "passport AB1234567", action=Action.REDACT)
    assert rule_ids(result) == {"pii.ph_passport", "pii.uk_passport"}
    assert result.redaction.content == "passport [REDACTED_SENSITIVE]"


def test_long_prose_completes_without_quadratic_patterns(checkers):
    text = ("Please explain the return policy for my recent order. " * 600)[:32000]
    result = check(checkers, text, timeout_ms=2000)
    assert result.evaluation_status == "complete" and not result.findings


@pytest.mark.parametrize("text", [("Call 202-555-0147 now. " * 1500)[:32000], ("a1b2c3d4e5f6" * 3000)[:32000]])
def test_long_inputs_never_pass_when_the_scan_runs_out_of_time(checkers, text):
    result = check(checkers, text, timeout_ms=50)
    pii = result.checker_results[0]
    assert pii.status in {"passed", "flagged", "error"}
    if pii.status == "error":
        assert pii.error_code in {"checker_timeout", "checker_failure"} and result.evaluation_status == "incomplete"


def test_lookahead_split_matches_the_pattern_as_written():
    import regex
    from guardoath.pii import split_lookaheads
    from guardoath.privacy_patterns import InPanRecognizer
    recognizer = InPanRecognizer()
    low = recognizer.patterns[-1]
    original = regex.compile(low.regex, recognizer.global_regex_flags)
    core, lookaheads = split_lookaheads(low.regex, recognizer.global_regex_flags, regex)
    rng = random.Random(11)
    for _ in range(2000):
        text = "".join(rng.choice("abcXYZ0123456789 @#-_\n") for _ in range(rng.randint(0, 60)))
        last = []
        for lookahead in lookaheads:
            starts = [m.start() for m in lookahead.finditer(text, overlapped=True)]
            last.append(starts[-1] if starts else -1)
        split = [m.span() for m in core.finditer(text) if m.start() <= min(last)]
        assert split == [m.span() for m in original.finditer(text)]


def test_skip_rules_use_pattern_length_and_phone_metadata():
    from guardoath.pii import fewest_phone_digits, shortest_match
    from guardoath.privacy_patterns import PhoneRecognizer, UrlRecognizer
    assert shortest_match(r"\d{3}-\d{2}") == 6 and shortest_match("(?") == 0
    assert fewest_phone_digits(PhoneRecognizer()) == 4
    assert fewest_phone_digits(PhoneRecognizer(supported_regions=("US",))) == 10
    assert fewest_phone_digits(UrlRecognizer()) == 0


def test_checker_version_names_the_copied_code(checkers):
    assert checkers["pii"].checker_version == "privacy-patterns-2.2.366/guardoath-patterns-6"


@pytest.mark.parametrize("text", ['<img src=x onerror="window.injected=true">', "Open index.html first.",
                                  "Read obj.prop here.", "Mr.Smith called.", "See test.com-x now."])
def test_url_needs_a_complete_top_level_domain(text):
    from guardoath.privacy_patterns import UrlRecognizer
    assert UrlRecognizer().analyze(text, ["URL"]) == []


@pytest.mark.parametrize("text, expected", [
    ("see example.com", "example.com"), ("Visit example.com.", "example.com"),
    ("Use example.com:8080", "example.com"), ("Try www.example.co.uk", "www.example.co.uk"),
    ("Join example.community", "example.community"), ("Ask shop.example.in", "shop.example.in"),
    ("Open https://example.com/path?x=1", "https://example.com/path?x=1"),
    ('<img src="https://example.com/a.png">', '"https://example.com/a.png"'),
])
def test_complete_urls_are_still_found(text, expected):
    from guardoath.privacy_patterns import UrlRecognizer
    found = {text[result.start:result.end] for result in UrlRecognizer().analyze(text, ["URL"])}
    assert expected in found


def test_markup_answer_without_a_url_passes_the_output_check(checkers):
    # The dashboard's "answer markup is displayed as text" example: no URL, no finding.
    policy = Policy(application_id="demo", version="v1", timeout_ms=500, rules=(Rule(checker_id="pii"),))
    request = CheckRequest(application_id="demo", policy_version="v1", content_format="text",
                           content='<img src=x onerror="window.injected=true">')
    result = asyncio.run(Engine([policy], checkers).check(request, Stage.OUTPUT))
    assert result.evaluation_status == "complete" and not result.findings


def test_an_iban_candidate_that_fails_validation_is_not_written_to_the_logs(caplog):
    import logging
    from guardoath.privacy_patterns import IbanRecognizer
    candidate = "DE89.3704.0044.0532.0130.00"  # dots are not removed, so the checksum step raises ValueError
    with caplog.at_level(logging.DEBUG, logger="guardoath.privacy"):
        assert IbanRecognizer().validate_result(candidate) is False
    assert "Failed to validate an IBAN candidate" in caplog.text
    assert candidate not in caplog.text and "3704" not in caplog.text
