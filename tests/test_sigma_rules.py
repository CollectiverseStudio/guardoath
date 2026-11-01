"""Unit tests for the conservative Sigma import foundation."""
import pytest
from guardoath.sigma_rules import import_rule, UnsupportedSigmaRule
from guardoath.sigma_rules.matcher import match_value, match_detection

def rule():
    return {
        "id": "ec570e53-4c76-45a9-804d-dc3f355ff7a7",
        "title": "7Zip Compressing Dump Files",
        "author": "Nasreddine Bencherchali (Nextron Systems)",
        "logsource": {"category": "process_creation", "product": "windows"},
        "detection": {
            "selection_img": [
                {"Description|contains": "7-Zip"},
                {"Image|endswith": [r"\7z.exe", r"\7zr.exe", r"\7za.exe"]},
                {"OriginalFileName": ["7z.exe", "7za.exe", "7zr.exe"]},
            ],
            "selection_extension": {"CommandLine|contains": [".dmp", ".dump", ".hdmp"]},
            "condition": "all of selection_*",
        },
    }

def test_real_upstream_rule_positive_negative():
    r = import_rule(rule(), "rules/windows/process_creation/proc_creation_win_7zip_exfil_dmp_files.yml")
    assert r.matches({"Image": r"C:\tools\7z.exe", "CommandLine": "7z a archive.zip crash.dmp"})
    assert not r.matches({"Image": r"C:\tools\7z.exe", "CommandLine": "7z a archive.zip report.txt"})
    assert not r.matches({"Image": r"C:\tools\notepad.exe", "CommandLine": "notepad crash.dmp"})
    assert r.finding()["author"] == "Nasreddine Bencherchali (Nextron Systems)"
    assert "github.com/SigmaHQ/sigma/blob/master/" in r.source_url

@pytest.mark.parametrize("actual,expected,modifiers", [
    ("AbCdef", "bc", ("contains",)),
    ("AbCdef", "abc", ("startswith",)),
    ("AbCdef", "def", ("endswith",)),
    (r"C:\7z.exe", r"\7z.exe", ("endswith",)),
    ("-encodedCommand", "/encodedCommand", ("windash",)),
])
def test_modifiers(actual, expected, modifiers):
    assert match_value(actual, expected, modifiers)

def test_all_of_and_one_of():
    d = {"selection_a": {"CommandLine|contains": "alpha"},
         "selection_b": {"CommandLine|contains": "beta"},
         "condition": "all of selection_*"}
    assert match_detection(d, {"CommandLine": "alpha beta"})
    assert not match_detection(d, {"CommandLine": "alpha"})
    d["condition"] = "1 of selection_*"
    assert match_detection(d, {"CommandLine": "alpha"})

def test_unsupported_condition_fails_explicitly():
    d = {"selection": {"CommandLine|contains": "x"},
         "filter": {"CommandLine|contains": "safe"},
         "condition": "selection and not filter"}
    with pytest.raises(UnsupportedSigmaRule):
        match_detection(d, {"CommandLine": "x"})

def test_unsupported_modifier_fails_explicitly():
    with pytest.raises(UnsupportedSigmaRule):
        match_value("abc", "a", ("unsupported",))

def test_missing_provenance_rejected():
    d = rule()
    del d["author"]
    with pytest.raises(UnsupportedSigmaRule):
        import_rule(d, "rules/windows/process_creation/example.yml")

def test_no_arbitrary_source_paths():
    with pytest.raises(UnsupportedSigmaRule):
        import_rule(rule(), "../../etc/passwd")

def test_unsupported_modifier_rejected_even_if_field_is_missing():
    from guardoath.sigma_rules.matcher import match_selection
    with pytest.raises(UnsupportedSigmaRule):
        match_selection({"CommandLine|unsupported": "x"}, {})

def test_invalid_selection_key_rejected():
    from guardoath.sigma_rules.matcher import match_selection
    with pytest.raises(UnsupportedSigmaRule):
        match_selection({None: "x"}, {})

def test_missing_supported_field_is_not_a_match():
    from guardoath.sigma_rules.matcher import match_selection
    assert not match_selection({"CommandLine|contains": "powershell"}, {})
