import pytest
pytest.importorskip("tldextract")
pytest.importorskip("phonenumbers")
from guardoath.privacy_patterns import UsHealthInsuranceMemberIdRecognizer

from privacy_assertions import assert_result


@pytest.fixture(scope="module")
def recognizer():
    """Return an instance of the US health insurance member ID recognizer."""
    return UsHealthInsuranceMemberIdRecognizer()


@pytest.fixture(scope="module")
def entity():
    """Return the US health insurance member ID entity name."""
    return "US_HEALTH_INSURANCE_MEMBER_ID"


def test_us_health_insurance_member_id_recognizer_metadata(recognizer, entity):
    """Test entity metadata and context without a recognizer threshold."""
    assert recognizer.supported_entities == [entity]
    assert recognizer.supported_language == "en"
    assert recognizer.context == ["member", "subscriber", "insurance", "policy"]
    assert recognizer.patterns[0].name == "Health insurance member ID (weak)"
    assert recognizer.patterns[0].score == 0.1
    assert recognizer.score_thresholds == {}
