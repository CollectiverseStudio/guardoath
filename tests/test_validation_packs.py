"""Offline tests for trusted validation-pack resolution; no detector claims."""
import pytest
from valinator.validation_packs import Pack, PackRegistry, PackSelectionError


def registry():
    return PackRegistry({
        "logistics": (
            Pack("LOG", "1", ("timestamps", "cross_field")),
            Pack("AGENT", "1", ("tool_calls", "timestamps")),
        ),
        "health": (Pack("HLTH", "1", ("pii",)),),
    })


def test_core_is_mandatory_and_overlap_runs_once():
    selected = registry().resolve("logistics", ("LOG", "AGENT"))
    assert PackRegistry.checker_union(("schema", "timestamps"), selected) == (
        "schema", "timestamps", "cross_field", "tool_calls"
    )


def test_no_optional_packs_preserves_core():
    assert PackRegistry.checker_union(("schema",), registry().resolve("logistics")) == ("schema",)


@pytest.mark.parametrize("codes", [
    ("HLTH",), ("UNKNOWN",), ("log",), ("CORE",), ("LOG", "LOG"),
    ("LOG;AGENT",), ("LOG ",), tuple(["LOG"] * 13),
])
def test_unknown_or_malformed_codes_are_rejected(codes):
    with pytest.raises(PackSelectionError):
        registry().resolve("logistics", codes)


def test_cross_tenant_isolation():
    with pytest.raises(PackSelectionError):
        registry().resolve("health", ("LOG",))


def test_bad_registry_is_rejected():
    with pytest.raises(ValueError):
        PackRegistry({"a": (Pack("CORE", "1", ("schema",)),)})
    with pytest.raises(ValueError):
        PackRegistry({"a": (Pack("LOG", "1", ("schema",)), Pack("LOG", "2", ("pii",)))})
