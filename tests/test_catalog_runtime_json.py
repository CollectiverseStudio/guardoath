"""Published catalog startup and the source writer must interpret identical bytes."""
import json

import pytest

from guardoath.catalog_metadata import load_release_history


@pytest.mark.parametrize("raw", [
    b'{"base_registry_version":"7","base_registry_version":"3","releases":[]}',
    b'{"base_registry_version":"3","releases":["hidden"],"releases":[]}',
    b'{"base_registry_version":NaN,"base_registry_version":"3","releases":[]}',
    b'{"base_registry_version":Infinity,"base_registry_version":"3","releases":[]}',
    b'{"base_registry_version":1e999,"base_registry_version":"3","releases":[]}',
])
def test_runtime_rejects_ambiguous_history_before_startup(tmp_path, raw):
    path = tmp_path / "history.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        load_release_history(path)


def test_runtime_history_reads_only_the_size_limit_plus_one(tmp_path, monkeypatch):
    from pathlib import Path
    from io import BytesIO

    class BoundedFile(BytesIO):
        def read(self, size=-1):
            assert size == 262145, "Catalog startup must bound the read itself"
            return super().read(size)

    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: BoundedFile(b" " * 262146))
    with pytest.raises(ValueError, match="Oversized"):
        load_release_history(tmp_path / "history.json")


def test_runtime_accepts_canonical_baseline_history(tmp_path):
    expected = {"base_registry_version": "3", "releases": []}
    path = tmp_path / "history.json"
    path.write_text(json.dumps(expected))
    assert load_release_history(path) == expected


@pytest.mark.parametrize("raw", [
    b'{"changes":{"title":{"before":"Hidden","before":"Reviewed","after":"New"}}}',
    b'{"changes":{"title":"first","title":"second"}}',
    b'{"x":1,"\\u0078":2}',
    b'{"nested":[NaN]}', b'{"nested":[Infinity]}', b'{"nested":[-Infinity]}',
    b'{"nested":[1e999]}', b'{"nested":[-1e999]}', b'{"x":"\xff"}',
])
def test_runtime_and_writer_share_recursive_strict_json_rules(raw):
    from guardoath.catalog_metadata import strict_catalog_json
    from guardoath.catalog_source_release import _strict_json, SourceReleaseConflict

    with pytest.raises(ValueError):
        strict_catalog_json(raw)
    with pytest.raises(SourceReleaseConflict):
        _strict_json(raw)


def test_shared_decoder_preserves_valid_finite_and_unicode_json():
    from guardoath.catalog_metadata import strict_catalog_json
    from guardoath.catalog_source_release import _strict_json

    raw = '{"name":"Reviewed — description","values":[1,1.25,true,null,{"x":"ok"}]}'
    assert strict_catalog_json(raw) == _strict_json(raw) == json.loads(raw)
