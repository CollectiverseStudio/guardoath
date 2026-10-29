"""Run every bundled local regression suite; no provider or deployment calls."""
import argparse
import asyncio
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path

from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.evaluate import evaluate, load_suite, render_html, write_outputs

SUITES = ('local', 'secrets', 'privacy', 'structure', 'business', 'text-security', 'bias', 'evidence', 'actions', 'encoded-privacy', 'privacy-patterns', 'ledger', 'timestamps', 'graph', 'integrity')


def render_index(collection):
    rows = []
    for row in collection['suites']:
        name = row['suite']
        cells = [str(row[k]) for k in ['total', 'matched', 'mismatched', 'incomplete', 'simulated']]
        rows.append('<tr><td><a href="' + escape(name, quote=True) + '/report.html">' + escape(name) +
                    '</a></td>' + ''.join('<td>' + escape(c) + '</td>' for c in cells) + '</tr>')
    total = collection['totals']
    return '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>All local GuardOath regression suites</title><style>
body{font:16px system-ui,sans-serif;color:#142135;background:#f6f8fb;margin:32px}
p{max-width:950px;line-height:1.6}.table{overflow:auto}table{border-collapse:collapse;background:white;min-width:650px}
th,td{padding:14px;text-align:left;border-bottom:1px solid #dde3ed}th{background:#142135;color:white}a{color:#185bb5}
</style><h1>All bundled local regression suites</h1><p><strong>''' + str(total['matched']) + ' of ' + str(total['total']) + ''' expectations matched.</strong></p>
<p>Matched expectations are not clean-content or accuracy claims. Some cases intentionally expect findings or incomplete assessments. This collection covers the bundled local software regressions, not live classifier accuracy, semantic fairness, all planned detectors, API/ingress tests or browser tests. No provider calls ran. Submitted, quoted and transformed text are omitted.</p>
<div class="table"><table><thead><tr><th>Suite</th><th>Cases</th><th>Matched</th><th>Mismatched</th><th>Incomplete</th><th>Simulated</th></tr></thead><tbody>''' + ''.join(rows) + '</tbody></table></div></html>'


async def run_all(root, destination, enable_pii=False):
    root, destination = Path(root).resolve(), Path(destination)
    if destination.is_symlink() or destination.exists():
        raise ValueError('A new collection directory is required')
    # Load and validate every suite/configuration before creating output. The
    # supplied provider environment is deliberately ignored.
    adapters = default_checkers(enable_pii=enable_pii)
    planned = []
    for name in SUITES:
        policy_file = 'playground.json' if name == 'local' else name + '-batch.json'
        suite = load_suite(root / 'evals' / (name + '.json'))
        engine = Engine(load_policies(root / 'policies' / policy_file), adapters)
        for case in suite.cases:
            if not engine.has_policy(case.request.application_id, case.request.policy_version):
                raise ValueError('Unknown regression policy')
            selected = next(p for p in engine.describe_policies(case.request.application_id) if p['version'] == case.request.policy_version)
            if any(c['external'] for c in selected['checks']):
                raise ValueError('Local regressions cannot select external checks')
        planned.append((name, suite, engine))
    destination.mkdir(parents=True, exist_ok=False)
    collection = {'format_version': 'collection-1', 'execution_scope': 'local_engine',
        'created_at': datetime.now(timezone.utc).isoformat(), 'suites': [],
        'totals': dict.fromkeys(['total', 'matched', 'mismatched', 'incomplete', 'simulated'], 0)}
    for name, suite, engine in planned:
        report = await evaluate(suite, engine)
        folder = destination / name
        folder.mkdir()
        write_outputs([(str(folder/'report.json'), json.dumps(report, indent=2)),
                       (str(folder/'report.html'), render_html(report))], False)
        row = {'suite': name, **report['summary']}
        collection['suites'].append(row)
        for key in collection['totals']:
            collection['totals'][key] += row[key]
    write_outputs([(str(destination/'collection.json'), json.dumps(collection, indent=2)),
                   (str(destination/'index.html'), render_index(collection))], False)
    return collection


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='.', help='Repository root containing evals and policies')
    parser.add_argument('--output', required=True, help='New collection directory; existing paths are never overwritten')
    parser.add_argument('--pii', action='store_true', help='Enable the offline sensitive-data checks for the starter and privacy suites')
    args = parser.parse_args(argv)
    try:
        collection = asyncio.run(run_all(args.root, args.output, args.pii))
        totals = collection['totals']
        print(f"{totals['matched']}/{totals['total']} expectations matched across {len(collection['suites'])} local suites; "
              f"{totals['mismatched']} mismatched; {totals['incomplete']} incomplete evaluations; {totals['simulated']} simulated cases.")
        return 1 if totals['mismatched'] else 0
    except Exception:
        print('Unable to finish all local regression suites. Check fixtures, policies, optional dependencies and the new output directory. Partial output is not a completed collection; no provider calls were enabled.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
