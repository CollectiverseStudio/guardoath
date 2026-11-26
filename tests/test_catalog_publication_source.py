"""Publication authority requires the exact bounded packaged source declaration."""
from io import BytesIO
import itertools
import json
from pathlib import Path

import pytest

from guardoath import state_contracts
from guardoath.catalog_publication_source import packaged_source_sha, PublicationSourceUnavailable

SHA = "a" * 40


def manifest():
    return state_contracts.release_metadata(SHA)


def test_valid_complete_packaged_manifest_is_source_authority(tmp_path):
    path = tmp_path / "_release_source.json"
    path.write_text(json.dumps(manifest(), sort_keys=True) + "\n")
    assert packaged_source_sha(path) == SHA


@pytest.mark.parametrize("mutation", [
    lambda x: x.pop("supported_state_contracts"),
    lambda x: x.update(extra="not the build declaration"),
    lambda x: x.update(source_sha="A" * 40),
    lambda x: x.update(source_sha="a" * 39),
    lambda x: x.update(source_sha=None),
    lambda x: x.update(release_contract_version=True),
    lambda x: x.update(release_contract_version=1.0),
    lambda x: x.update(release_contract_version=2),
    lambda x: x.update(introduced_state_contracts=[]),
    lambda x: x.update(introduced_state_contracts=["catalog-publication-v1"]),
    lambda x: x.update(introduced_state_contracts=["catalog-publication-lifecycle-v1"]),
    lambda x: x["introduced_state_contracts"].remove("subscription-cap-provenance-v1"),
    lambda x: x["introduced_state_contracts"].remove("stripe-period-end-cancellation-v1"),
    lambda x: x.update(supported_state_contracts=["catalog-publication-v1"]),
    lambda x: x["supported_state_contracts"].append("catalog-publication-v1"),
    lambda x: x["supported_state_contracts"].reverse(),
])
def test_partial_or_different_contracts_cannot_supply_publication_authority(tmp_path, mutation):
    value = manifest()
    mutation(value)
    path = tmp_path / "_release_source.json"
    path.write_text(json.dumps(value))
    with pytest.raises(PublicationSourceUnavailable):
        packaged_source_sha(path)


@pytest.mark.parametrize("raw", [
    b'{"source_sha":"' + b"a" * 40 + b'"}',
    b'[]', b'null', b'"string"', b'{}', b'\xff',
    b'{"source_sha":NaN}', b'{"source_sha":Infinity}', b'{"source_sha":1e999}',
    b" " * 4097,
])
def test_invalid_or_oversized_manifest_never_falls_back_to_environment(tmp_path, monkeypatch, raw):
    monkeypatch.setenv("GUARDOATH_SOURCE_SHA", SHA)
    monkeypatch.setenv("GUARDOATH_RELEASE_SOURCE_SHA", SHA)
    path = tmp_path / "_release_source.json"
    path.write_bytes(raw)
    with pytest.raises(PublicationSourceUnavailable):
        packaged_source_sha(path)


def test_valid_last_duplicate_sha_is_rejected(tmp_path):
    path = tmp_path / "_release_source.json"
    raw = json.dumps(manifest())
    path.write_text(raw.replace('"source_sha":', '"source_sha":"' + "b" * 40 + '","source_sha":', 1))
    with pytest.raises(PublicationSourceUnavailable):
        packaged_source_sha(path)


def test_packaged_source_read_bounds_the_allocation_itself(tmp_path, monkeypatch):
    path = tmp_path / "_release_source.json"
    path.write_text("placeholder")
    sizes = []

    class BoundedRead(BytesIO):
        def read(self, size=-1):
            sizes.append(size)
            assert size == 4097
            return super().read(size)

    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: BoundedRead(b" " * 10000))
    with pytest.raises(PublicationSourceUnavailable):
        packaged_source_sha(path)
    assert sizes == [4097]


def test_missing_nonfile_and_symlink_provenance_are_refused(tmp_path):
    absent = tmp_path / "absent.json"
    with pytest.raises(PublicationSourceUnavailable):
        packaged_source_sha(absent)
    with pytest.raises(PublicationSourceUnavailable):
        packaged_source_sha(tmp_path)
    actual = tmp_path / "manifest.json"
    actual.write_text(json.dumps(manifest()))
    link = tmp_path / "_release_source.json"
    link.symlink_to(actual)
    with pytest.raises(PublicationSourceUnavailable):
        packaged_source_sha(link)


def test_activated_source_retains_both_catalog_contracts_with_financial_activation(monkeypatch):
    for name in ("ENABLE_NEW_CATALOG_PUBLICATION_STATE", "GUARDOATH_ENABLE_NEW_CATALOG_PUBLICATION_STATE",
                 "ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE",
                 "GUARDOATH_ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE",
                 "GUARDOATH_CATALOG_PUBLICATION_ENABLED", "GUARDOATH_ADMIN_TEST_WRITER_PHASE"):
        monkeypatch.setenv(name, "0")
    assert state_contracts.ENABLE_NEW_CATALOG_PUBLICATION_STATE is True
    assert state_contracts.ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE is True
    assert state_contracts.new_catalog_publication_state_enabled() is True
    assert state_contracts.new_catalog_publication_lifecycle_state_enabled() is True
    assert state_contracts.ENABLE_NEW_SUBSCRIPTION_STATE is True
    assert state_contracts.ENABLE_NEW_CANCELLATION_STATE is True
    assert state_contracts.new_subscription_state_enabled() is True
    assert state_contracts.new_cancellation_state_enabled() is True
    assert manifest()["introduced_state_contracts"] == [
        "subscription-cap-provenance-v1", "stripe-period-end-cancellation-v1",
        "catalog-publication-v1", "catalog-publication-lifecycle-v1"]


def test_explicit_catalog_reader_phase_cannot_be_enabled_by_environment(monkeypatch):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_STATE", False)
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE", False)
    assert state_contracts.ENABLE_NEW_CATALOG_PUBLICATION_STATE is False
    for name in ("ENABLE_NEW_CATALOG_PUBLICATION_STATE", "GUARDOATH_ENABLE_NEW_CATALOG_PUBLICATION_STATE",
                 "GUARDOATH_CATALOG_PUBLICATION_ENABLED", "GUARDOATH_ADMIN_TEST_WRITER_PHASE"):
        monkeypatch.setenv(name, "1")
    assert state_contracts.new_catalog_publication_state_enabled() is False
    assert manifest()["introduced_state_contracts"] == [
        "subscription-cap-provenance-v1", "stripe-period-end-cancellation-v1"]
    assert "catalog-publication-v1" in manifest()["supported_state_contracts"]


@pytest.mark.parametrize("caps,cancellation,catalog,lifecycle", list(itertools.product([False, True], repeat=4)))
def test_all_writer_contracts_are_independently_declared(monkeypatch, caps, cancellation, catalog, lifecycle):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_SUBSCRIPTION_STATE", caps)
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CANCELLATION_STATE", cancellation)
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_STATE", catalog)
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE", lifecycle)
    declaration = manifest()
    assert declaration["supported_state_contracts"] == [
        "subscription-cap-provenance-v1", "stripe-period-end-cancellation-v1",
        "catalog-publication-v1", "catalog-publication-lifecycle-v1", "stripe-invoice-refund-v1"]
    assert declaration["introduced_state_contracts"] == [contract for enabled, contract in (
        (caps, "subscription-cap-provenance-v1"),
        (cancellation, "stripe-period-end-cancellation-v1"),
        (catalog, "catalog-publication-v1"),
        (lifecycle, "catalog-publication-lifecycle-v1")) if enabled]


@pytest.mark.parametrize("invalid", [None, 0, 1, "true", [], {}])
def test_catalog_writer_gate_requires_real_boolean(monkeypatch, invalid):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_STATE", invalid)
    with pytest.raises(ValueError):
        state_contracts.new_catalog_publication_state_enabled()
    with pytest.raises(ValueError):
        manifest()
