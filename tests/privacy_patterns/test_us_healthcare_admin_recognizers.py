import pytest
pytest.importorskip("tldextract")
pytest.importorskip("phonenumbers")
from guardoath.privacy_patterns import PatternRecognizer
from guardoath.privacy_patterns import UsClaimNumberRecognizer, UsPrescriptionNumberRecognizer, UsPriorAuthorizationNumberRecognizer, UsProviderTaxIdRecognizer, UsReferralNumberRecognizer

from privacy_assertions import assert_result


@pytest.mark.parametrize(
    "recognizer, entity, expected_context",
    [
        (
            UsPriorAuthorizationNumberRecognizer(),
            "US_PRIOR_AUTHORIZATION_NUMBER",
            ["authorization", "auth", "preauthorization", "approval"],
        ),
        (
            UsClaimNumberRecognizer(),
            "US_CLAIM_NUMBER",
            ["claim", "billing"],
        ),
        (
            UsPrescriptionNumberRecognizer(),
            "US_PRESCRIPTION_NUMBER",
            ["prescription", "pharmacy", "medication"],
        ),
        (
            UsReferralNumberRecognizer(),
            "US_REFERRAL_NUMBER",
            ["referral", "infusion", "specialty", "referring"],
        ),
        (
            UsProviderTaxIdRecognizer(),
            "US_PROVIDER_TAX_ID",
            ["tax", "tin", "ein", "billing"],
        ),
    ],
)
def test_us_healthcare_admin_recognizer_metadata(recognizer, entity, expected_context):
    """Test entity metadata and context without a recognizer threshold."""
    assert isinstance(recognizer, PatternRecognizer)
    assert PatternRecognizer in type(recognizer).__bases__
    assert recognizer.COUNTRY_CODE == "us"
    assert recognizer.supported_entities == [entity]
    assert recognizer.supported_language == "en"
    assert recognizer.context == expected_context
    assert recognizer.score_thresholds == {}
