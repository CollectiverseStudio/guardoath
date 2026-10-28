"""Exercise the real launcher process without bootstrapping or external traffic."""

import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'start_dashboard.py'
PII_AVAILABLE = importlib.util.find_spec('presidio_analyzer') is not None and sys.version_info[:2] < (3,15)


def test_help_does_not_install_or_start_server():
    result = subprocess.run([sys.executable,str(SCRIPT),'--help'],capture_output=True,text=True,timeout=10)
    assert result.returncode == 0
    assert '--no-browser' in result.stdout and '--reinstall' in result.stdout
    assert '--serve' not in result.stdout


@pytest.mark.parametrize('port', ['0','65536','bad'])
def test_invalid_port_fails_before_setup(port):
    result = subprocess.run([sys.executable,str(SCRIPT),'--port',port],capture_output=True,text=True,timeout=10)
    assert result.returncode == 2
    assert 'Choose a port' in result.stderr


@pytest.mark.skipif(not PII_AVAILABLE, reason='Complete launcher requires optional PII dependencies')
def test_launcher_serves_dashboard_and_all_policies_with_fresh_key():
    with socket.socket() as allocation:
        allocation.bind(('127.0.0.1',0))
        port = allocation.getsockname()[1]
    environment = {**os.environ, 'VALINATOR_API_KEY':'old-key', 'VALINATOR_POLICY_FILE':'missing-old-policy',
                   'VALINATOR_ENABLE_PII':'0', 'VALINATOR_ENABLE_AZURE_PROMPT_SHIELDS':'1',
                   'VALINATOR_AZURE_CONTENT_SAFETY_ENDPOINT':'invalid-old-endpoint'}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    process = subprocess.Popen([sys.executable,str(SCRIPT),'--serve','--no-browser','--port',str(port)],
                               cwd=ROOT.parent,env=environment,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        deadline = time.monotonic()+20
        while time.monotonic()<deadline:
            try:
                with opener.open(f'http://127.0.0.1:{port}/healthz',timeout=.5) as response:
                    assert response.status == 200
                    break
            except (OSError,urllib.error.URLError):
                if process.poll() is not None:
                    pytest.fail('Launcher exited before readiness (output omitted to protect access key)')
                time.sleep(.1)
        else:
            pytest.fail('Launcher did not become ready')
        # Read only the pre-server, flushed banner. Never print the generated key.
        key = None
        for _ in range(5):
            line = process.stdout.readline()
            if line.startswith('Application API key: '):
                key = line.strip().split(': ',1)[1]
                break
        assert key and key != 'old-key' and len(key) >= 32
        with opener.open(f'http://127.0.0.1:{port}/dashboard',timeout=2) as response:
            assert b'Check playground' in response.read()
        request = urllib.request.Request(f'http://127.0.0.1:{port}/v1/applications/current',
                                         headers={'Authorization':'Bearer '+key})
        with opener.open(request,timeout=2) as response:
            metadata = json.load(response)
        assert {'pii-v1','redaction-v1','secrets-v1','business-v1','completeness-v1','text-security-v1'} <= {p['version'] for p in metadata['policies']}
        with pytest.raises(urllib.error.HTTPError) as denied:
            opener.open(urllib.request.Request(request.full_url,headers={'Authorization':'Bearer old-key'}),timeout=2)
        assert denied.value.code == 401
    finally:
        process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)


@pytest.mark.skipif(not PII_AVAILABLE, reason='Complete launcher requires optional PII dependencies')
def test_launcher_refuses_occupied_port_without_printing_key():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0))
        listener.listen()
        result = subprocess.run([sys.executable,str(SCRIPT),'--serve','--no-browser','--port',str(listener.getsockname()[1])],
                                cwd=ROOT,capture_output=True,text=True,timeout=20)
    assert result.returncode == 1
    assert 'Cannot use port' in result.stderr
    assert 'Application API key:' not in result.stdout


@pytest.mark.skipif(not PII_AVAILABLE, reason='Complete evaluation requires optional PII dependencies')
def test_launcher_evaluation_creates_a_report_without_starting_server(tmp_path,monkeypatch):
    import start_dashboard
    (tmp_path/'evals').mkdir()
    (tmp_path/'policies').mkdir()
    (tmp_path/'evals/local.json').write_text((ROOT/'evals/local.json').read_text(encoding='utf-8'),encoding='utf-8')
    (tmp_path/'policies/playground.json').write_text((ROOT/'policies/playground.json').read_text(encoding='utf-8'),encoding='utf-8')
    monkeypatch.setattr(start_dashboard,'ROOT',tmp_path)
    monkeypatch.setattr(start_dashboard,'prepare_environment',lambda reinstall=False: Path(sys.executable))
    monkeypatch.setattr(sys,'argv',['start_dashboard.py','--evaluate','--no-browser'])
    assert start_dashboard.main() == 0
    reports = list((tmp_path/'.valinator-reports').glob('*/report.json'))
    assert len(reports) == 1
    assert json.loads(reports[0].read_text())['summary']['matched'] == 30
    assert reports[0].with_name('report.html').is_file()


@pytest.mark.skipif(not PII_AVAILABLE, reason='Complete evaluation requires optional PII dependencies')
def test_launcher_second_run_compares_with_previous_without_overwriting_it(tmp_path,monkeypatch):
    import start_dashboard
    (tmp_path/'evals').mkdir()
    (tmp_path/'policies').mkdir()
    (tmp_path/'evals/local.json').write_text((ROOT/'evals/local.json').read_text(encoding='utf-8'),encoding='utf-8')
    (tmp_path/'policies/playground.json').write_text((ROOT/'policies/playground.json').read_text(encoding='utf-8'),encoding='utf-8')
    monkeypatch.setattr(start_dashboard,'ROOT',tmp_path)
    monkeypatch.setattr(start_dashboard,'prepare_environment',lambda reinstall=False: Path(sys.executable))
    monkeypatch.setattr(sys,'argv',['start_dashboard.py','--evaluate','--no-browser'])
    assert start_dashboard.main()==0
    first=next((tmp_path/'.valinator-reports').glob('*/report.json'))
    original=first.read_bytes()
    assert start_dashboard.main()==0
    comparisons=list((tmp_path/'.valinator-reports').glob('*/comparison.json'))
    assert len(comparisons)==1
    assert json.loads(comparisons[0].read_text())['summary']['unchanged']==30
    assert first.read_bytes()==original
