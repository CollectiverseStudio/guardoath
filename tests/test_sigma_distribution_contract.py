"""Archive-only Sigma acceptance: fixtures use exact original assets, never archive code."""
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import warnings
import zipfile

import pytest

from guardoath.sigma_rules import verify_distribution as distribution
from guardoath.sigma_rules.evidence_catalog import BATCH_NAMES

ROOT=Path(__file__).resolve().parents[1]/'guardoath/sigma_rules'
PREFIX=distribution.PREFIX
ASSETS={PREFIX+n:(ROOT/n).read_bytes() for n in BATCH_NAMES}
ASSETS.update({PREFIX+str(p.relative_to(ROOT)).replace('\\','/'):p.read_bytes()
    for p in (ROOT/'upstream').rglob('*.yml')})
ASSETS[PREFIX+'LICENSE.Detection.Rules.md']=(ROOT/'LICENSE.Detection.Rules.md').read_bytes()
# If verifier executed any archive module, even the complete fixture would fail.
ASSETS[PREFIX+'__init__.py']=b'raise AssertionError("archive code executed")\n'
SNAPSHOT=next(n for n in ASSETS if '/upstream/' in n)


def wheel(tmp_path,assets=None,*,extra=()):
    path=tmp_path/'fixture.whl'
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,data in (ASSETS if assets is None else assets).items():archive.writestr(name,data)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',UserWarning)
            for name,data in extra:
                # ZipInfo normally rewrites os.sep on Windows. Preserve the
                # malformed raw header name so every platform tests the same ZIP.
                info=zipfile.ZipInfo('fixture')
                info.filename=info.orig_filename=name
                archive.writestr(info,data)
    return path


def test_complete_assets_are_verified_without_executing_code_or_using_checkout(tmp_path,monkeypatch):
    from guardoath.sigma_rules import evidence_catalog
    monkeypatch.setattr(evidence_catalog,'load_evidence_catalog',lambda:pytest.fail('checkout fallback used'))
    report=distribution.verify_wheel(wheel(tmp_path))
    assert (report.rule_count,report.pinned_rule_count,report.required_asset_count)==(128,125,132)
    assert report.source_revisions==('05d1031f537e830b76da53bae2400c5814c89165',)
    assert report.license_sha256==distribution.LICENSE_SHA256
    with pytest.raises(FrozenInstanceError):report.rule_count=0


@pytest.mark.parametrize('name',list(ASSETS)[:-1])
def test_each_missing_required_asset_fails_despite_checkout_having_it(tmp_path,name):
    assets=dict(ASSETS);assets.pop(name)
    with pytest.raises(distribution.DistributionContractError,match='missing'):
        distribution.verify_wheel(wheel(tmp_path,assets))


@pytest.mark.parametrize('name',[SNAPSHOT,PREFIX+'LICENSE.Detection.Rules.md'])
def test_changed_snapshot_or_license_fails(tmp_path,name):
    assets=dict(ASSETS);assets[name]+=b'changed\n'
    with pytest.raises(distribution.DistributionContractError):distribution.verify_wheel(wheel(tmp_path,assets))


def test_windows_line_endings_remain_canonical_git_text(tmp_path):
    assets={name:data.decode('utf-8').replace('\r\n','\n').replace('\n','\r\n').encode('utf-8')
        for name,data in ASSETS.items()}
    assert distribution.verify_wheel(wheel(tmp_path,assets)).pinned_rule_count==125


@pytest.mark.parametrize('raw',[b'{bad',b'{"license":"first","license":"second"}',b'\xff',b'{"x":NaN}'])
def test_malformed_json_duplicate_names_nonfinite_or_bad_encoding_fails(tmp_path,raw):
    assets=dict(ASSETS);assets[PREFIX+BATCH_NAMES[0]]=raw
    with pytest.raises(distribution.DistributionContractError):distribution.verify_wheel(wheel(tmp_path,assets))


@pytest.mark.parametrize('field,value',[('id',''),('source_git_blob','0'*40),('source_sha256','0'*64)])
def test_inconsistent_metadata_cannot_be_accepted(tmp_path,field,value):
    assets=dict(ASSETS);batch=json.loads(assets[PREFIX+BATCH_NAMES[-1]])
    batch['rules'][0][field]=value;assets[PREFIX+BATCH_NAMES[-1]]=json.dumps(batch).encode()
    with pytest.raises(distribution.DistributionContractError):distribution.verify_wheel(wheel(tmp_path,assets))


@pytest.mark.parametrize('name',['../escape','/absolute','C:/absolute','a\\b','a/./b','a//b','a\x00b'])
def test_unsafe_member_paths_are_rejected_without_extraction(tmp_path,name):
    path=wheel(tmp_path,extra=[(name,b'ignored')])
    with zipfile.ZipFile(path) as archive:
        assert archive.infolist()[-1].orig_filename==name
    with pytest.raises(distribution.DistributionContractError,match='member'):
        distribution.verify_wheel(path)


@pytest.mark.parametrize('name',[PREFIX+BATCH_NAMES[0],(PREFIX+BATCH_NAMES[0]).upper()])
def test_duplicate_or_case_colliding_members_fail(tmp_path,name):
    with pytest.raises(distribution.DistributionContractError,match='member'):
        distribution.verify_wheel(wheel(tmp_path,extra=[(name,b'ignored')]))


@pytest.mark.parametrize('name',[PREFIX+'upstream_batch_007.json',PREFIX+'upstream/batch_005/orphan.yml'])
def test_new_unindexed_batches_or_snapshots_are_not_silently_omitted(tmp_path,name):
    with pytest.raises(distribution.DistributionContractError):
        distribution.verify_wheel(wheel(tmp_path,extra=[(name,b'{}')]))


@pytest.mark.parametrize('limit', ['MAX_ASSET_BYTES','MAX_REQUIRED_BYTES','MAX_WHEEL_BYTES','MAX_MEMBERS'])
def test_resource_limits_fail_before_success(tmp_path,monkeypatch,limit):
    path=wheel(tmp_path);monkeypatch.setattr(distribution,limit,1)
    with pytest.raises(distribution.DistributionContractError):distribution.verify_wheel(path)


def test_empty_data_wheel_cannot_fall_back_to_source_tree(tmp_path):
    with pytest.raises(distribution.DistributionContractError,match='missing'):
        distribution.verify_wheel(wheel(tmp_path,{PREFIX+'__init__.py':b''}))


@pytest.mark.parametrize('exists',[False,True])
def test_missing_or_invalid_archive_is_sanitized(tmp_path,exists):
    path=tmp_path/'private-canary.whl'
    if exists:path.write_bytes(b'private-canary')
    with pytest.raises(distribution.DistributionContractError) as error:distribution.verify_wheel(path)
    assert 'private-canary' not in str(error.value)


def test_cli_contract_complete_failure_and_usage(tmp_path,capsys):
    path=wheel(tmp_path)
    assert distribution.main([str(path)])==0
    assert json.loads(capsys.readouterr().out)['rule_count']==128
    path.write_bytes(b'bad archive')
    assert distribution.main([str(path)])==1
    captured=capsys.readouterr();assert captured.out=='' and 'verification failed' in captured.err
    assert distribution.main([])==2
