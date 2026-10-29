"""Run the complete local dashboard: python start_dashboard.py [--port 8001]."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
from datetime import datetime, timezone
import time
import urllib.error
import urllib.request
import venv
import webbrowser

ROOT = Path(__file__).resolve().parent
ENVIRONMENT = ROOT / '.dashboard-venv'


def port_number(value):
    try:
        number = int(value)
        if 1 <= number <= 65535:
            return number
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('Choose a port between 1 and 65535')


def prepare_environment(reinstall=False):
    interpreter = ENVIRONMENT / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    fingerprint = hashlib.sha256((ROOT / 'pyproject.toml').read_bytes() +
                                 sys.version.encode() + sys.executable.encode()).hexdigest()
    marker = ENVIRONMENT / '.dashboard-setup'
    if not interpreter.is_file():
        print('Preparing the dashboard environment. First launch needs internet access.', flush=True)
        venv.EnvBuilder(with_pip=True).create(ENVIRONMENT)
    if reinstall or not marker.is_file() or marker.read_text() != fingerprint:
        # Invalidate before installation: interrupted setup must be retried.
        marker.unlink(missing_ok=True)
        print('Installing dashboard dependencies. This can take several minutes.', flush=True)
        subprocess.run([str(interpreter), '-m', 'pip', 'install', '-e', '.[pii]'], cwd=ROOT, check=True)
        subprocess.run([str(interpreter), '-m', 'pip', 'check'], cwd=ROOT, check=True)
        marker.write_text(fingerprint)
    return interpreter


def open_when_ready(url):
    # Do not send localhost requests through an inherited HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with opener.open(url.replace('/dashboard', '/healthz'), timeout=.5) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(.1)


def serve(port, browser=True):
    key = secrets.token_urlsafe(32)
    # Deliberately choose the complete local profile, independent of old setup.
    os.environ.update(VALINATOR_API_KEY=key, VALINATOR_ENABLE_PII='1',
                      VALINATOR_ENABLE_AZURE_PROMPT_SHIELDS='0', VALINATOR_CONTENT_SAFETY_PROVIDER='none',
                      VALINATOR_POLICY_FILE=str(ROOT / 'policies/playground.json'))
    try:
        import uvicorn
        from valinator.app import app
    except ImportError:
        print('Dashboard dependencies are missing. Run python start_dashboard.py --reinstall.', file=sys.stderr)
        return 1
    url = f'http://127.0.0.1:{port}/dashboard'
    # Bind before opening the browser so an occupied port cannot open another service.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind(('127.0.0.1', port))
        except OSError:
            print(f'Cannot use port {port}. Close the other server or try --port {port + 1 if port < 65535 else 8000}.', file=sys.stderr)
            return 1
        print(f'\nDashboard: {url}\nApplication API key: {key}\nPaste the key into the dashboard and click Connect.\nKeep this terminal open. Press Ctrl+C to stop.\n', flush=True)
        if browser:
            threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, access_log=False))
        server.run(sockets=[listener])
        return 0 if server.started else 1


def main():
    parser = argparse.ArgumentParser(description='Prepare and open the complete local Valinator dashboard.')
    parser.add_argument('--port', type=port_number, default=8000)
    parser.add_argument('--no-browser', action='store_true', help='Print the URL without opening a browser')
    parser.add_argument('--reinstall', action='store_true', help='Reinstall the dashboard dependencies')
    parser.add_argument('--evaluate', action='store_true', help='Run the bundled local regression cases and open their report')
    parser.add_argument('--suite', choices=['local','secrets','privacy','structure','business','text-security','bias','evidence','actions','all'], default='local', help='Choose starter examples or a complete group batch')
    parser.add_argument('--baseline', help='Compare an evaluation against this saved report')
    parser.add_argument('--no-compare', action='store_true', help='Skip automatic comparison with a previous evaluation')
    parser.add_argument('--serve', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.suite != 'local' and not args.evaluate:
        parser.error('--suite requires --evaluate')
    if (args.baseline or args.no_compare) and not args.evaluate:
        parser.error('--baseline and --no-compare require --evaluate')
    if args.suite == 'all' and args.baseline:
        parser.error('--baseline is for individual suites; all-suite collections preserve separate reports')
    if args.baseline and args.no_compare:
        parser.error('Choose --baseline or --no-compare')
    if args.evaluate and args.serve:
        parser.error('--evaluate cannot be combined with internal server mode')
    if not (3, 11) <= sys.version_info[:2] < (3, 15):
        print('The complete dashboard needs Python 3.11–3.14 for its PII detector. Use Python 3.12 or 3.14.', file=sys.stderr)
        return 1
    try:
        if args.serve:
            return serve(args.port, not args.no_browser)
        interpreter = prepare_environment(args.reinstall)
        if args.evaluate and args.suite == 'all':
            run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-all-') + secrets.token_hex(4)
            directory = ROOT / '.valinator-reports' / run_id
            command = [str(interpreter), '-m', 'valinator.evaluate_all', '--pii', '--root', str(ROOT), '--output', str(directory)]
            completed = subprocess.run(command, cwd=ROOT)
            if completed.returncode in {0, 1} and (directory/'index.html').is_file():
                print(f'All local regression reports: {directory / "index.html"}', flush=True)
                if not args.no_browser:
                    webbrowser.open((directory/'index.html').as_uri())
            return completed.returncode
        if args.evaluate:
            cases_path = ROOT / 'evals' / (args.suite + '.json')
            policies_path = ROOT / 'policies' / ({'secrets':'secrets-batch.json','privacy':'privacy-batch.json','structure':'structure-batch.json','business':'business-batch.json','text-security':'text-security-batch.json','bias':'bias-batch.json','evidence':'evidence-batch.json','actions':'actions-batch.json'}.get(args.suite,'playground.json'))
            baseline = Path(args.baseline).resolve() if args.baseline else None
            if not baseline and not args.no_compare:
                previous = sorted((ROOT / '.valinator-reports').glob('*/report.json'),
                                  key=lambda p:p.stat().st_mtime_ns,reverse=True)
                # Candidate selection uses only bounded metadata; the child validates
                # the chosen report fully before running cases or producing output.
                suite_id = json.loads(cases_path.read_text(encoding='utf-8'))['suite_id']
                for candidate in previous:
                    try:
                        with candidate.open('rb') as stream:
                            raw = stream.read(262145)
                        if len(raw) <= 262144 and json.loads(raw.decode('utf-8')).get('suite_id') == suite_id:
                            baseline = candidate
                            break
                    except (OSError,ValueError,AttributeError,RecursionError):
                        continue
            run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + secrets.token_hex(4)
            directory = ROOT / '.valinator-reports' / run_id
            directory.mkdir(parents=True)
            html_path = directory / 'report.html'
            command = [str(interpreter), '-m', 'valinator.evaluate', '--pii',
                       '--cases', str(cases_path),
                       '--policies', str(policies_path),
                       '--report', str(directory / 'report.json'), '--html', str(html_path)]
            if baseline:
                command.extend(['--baseline',str(baseline),'--comparison-report',str(directory / 'comparison.json')])
                print(f'Comparing with: {baseline}',flush=True)
            elif not args.no_compare:
                print('No previous matching report. This run can be used as a baseline.',flush=True)
            completed = subprocess.run(command, cwd=ROOT)
            if completed.returncode in {0, 1} and html_path.is_file():
                print(f'Regression report: {html_path}', flush=True)
                if not args.no_browser:
                    webbrowser.open(html_path.as_uri())
            return completed.returncode
        command = [str(interpreter), str(Path(__file__).resolve()), '--serve', '--port', str(args.port)]
        if args.no_browser:
            command.append('--no-browser')
        return subprocess.run(command, cwd=ROOT).returncode
    except KeyboardInterrupt:
        return 130
    except (OSError, subprocess.CalledProcessError):
        print('Dashboard setup could not finish. Check your Python installation/network, then retry with --reinstall.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
