"""Local-engine regression assertions. Reports exclude prompts and redacted text."""

import argparse
import asyncio
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from valinator.checkers import default_checkers
from valinator.config import load_policies
from valinator.engine import Engine
from valinator.json_data import MAX_JSON_BYTES, parse_json
from valinator.models import Action, CheckRequest, Contract, Stage

Status = Literal['passed','flagged','error','skipped','unsupported']


class Expected(Contract):
    evaluation_status: Literal['complete','incomplete']
    recommended_action: Action
    effective_action: Action
    checker_statuses: dict[str, Status] = Field(min_length=1, max_length=50)
    finding_rule_ids: tuple[str, ...] = Field(default=(), max_length=100)
    redaction_status: Literal['not_requested','not_needed','preview','ready','withheld'] = 'not_requested'
    simulated: bool = False


class EvaluationCase(Contract):
    case_id: str = Field(pattern=r'^[a-zA-Z0-9_.-]{1,80}$')
    group: str = Field(pattern=r'^[a-zA-Z0-9_.-]{1,80}$')
    stage: Stage
    request: CheckRequest
    expected: Expected


class Suite(Contract):
    format_version: Literal['1']
    suite_id: str = Field(pattern=r'^[a-zA-Z0-9_.-]{1,80}$')
    cases: tuple[EvaluationCase, ...] = Field(min_length=1, max_length=100)


def load_suite(path):
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise ValueError('Suite too large')
        suite = Suite.model_validate(parse_json(raw.decode('utf-8')))
        if len({case.case_id for case in suite.cases}) != len(suite.cases):
            raise ValueError('Duplicate case identifiers')
        return suite
    except Exception:
        raise ValueError('Unable to load the bounded regression suite') from None


async def evaluate(suite, engine, groups=None):
    cases = [c for c in suite.cases if not groups or c.group in groups]
    if not cases:
        raise ValueError('No cases matched the selected groups')
    # Validate references before executing anything; avoid misleading partial reports.
    if any(not engine.has_policy(c.request.application_id,c.request.policy_version) for c in cases):
        raise ValueError('A case references an unavailable policy')
    for case in cases:
        description = next(p for p in engine.describe_policies(case.request.application_id)
                           if p['version'] == case.request.policy_version)
        if any(c['external'] for c in description['checks']):
            raise ValueError('Regression runs only support local adapters')
    rows = []
    for case in cases:
        result = await engine.check(case.request,case.stage)
        actual = {
            'evaluation_status':result.evaluation_status,
            'recommended_action':result.recommended_action.value,
            'effective_action':result.effective_action.value,
            'checker_statuses':{r.checker_id:r.status for r in result.checker_results},
            'finding_rule_ids':sorted({f.rule_id for f in result.findings}),
            'redaction_status':result.redaction.status,
            'simulated':result.simulated,
        }
        expected = case.expected.model_dump(mode='json')
        expected['finding_rule_ids'] = sorted(set(expected['finding_rule_ids']))
        differences = [field for field in actual if actual[field] != expected[field]]
        rows.append({'case_id':case.case_id,'group':case.group,'stage':case.stage.value,
                     'matched':not differences,'differences':differences,
                     **actual,'latency_ms':round(result.total_latency_ms,3)})
    summary = {'total':len(rows), 'matched':sum(r['matched'] for r in rows),
               'mismatched':sum(not r['matched'] for r in rows),
               'incomplete':sum(r['evaluation_status'] == 'incomplete' for r in rows),
               'simulated':sum(r['simulated'] for r in rows)}
    return {'format_version':'1','suite_id':suite.suite_id,'execution_scope':'local_engine',
            'created_at':datetime.now(timezone.utc).isoformat(), 'summary':summary,'cases':rows}


def render_html(report):
    summary = report['summary']
    rows = []
    for row in report['cases']:
        cells = [row['case_id'],row['group'],row['stage'], 'Matched' if row['matched'] else 'MISMATCH',
                 row['evaluation_status'],row['recommended_action'],row['effective_action'],
                 'Yes' if row['simulated'] else 'No',row['redaction_status'],
                 ', '.join(row['differences']) or '—',', '.join(row['finding_rule_ids']) or '—',
                 ', '.join(f'{k}: {v}' for k,v in row['checker_statuses'].items()),str(row['latency_ms'])]
        state = 'bad' if not row['matched'] else 'warn' if row['simulated'] or row['evaluation_status'] == 'incomplete' else 'matched'
        rows.append('<tr class="'+state+'">'+''.join('<td>'+escape(str(cell))+'</td>' for cell in cells)+'</tr>')
    titles = ['Case','Group','Stage','Expectation','Evaluation','Recommended','Effective','Simulated','Redaction','Differences','Finding IDs','Checkers','ms']
    return '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Valinator regression report</title><style>
body{font:15px system-ui,sans-serif;color:#142135;background:#f6f8fb;margin:32px}
h1{margin-bottom:8px}p{max-width:1000px;line-height:1.5}.table{overflow:auto;background:white;border:1px solid #d8dfeb}
table{border-collapse:collapse;min-width:100%;font-size:13px}th,td{padding:12px;text-align:left;border-bottom:1px solid #dde3ed;vertical-align:top}th{background:#142135;color:white}.bad{background:#fff0ef}.warn{background:#fff7df}
</style><h1>Valinator regression report</h1><p>Suite: '''+escape(report['suite_id'])+'''</p><p><strong>'''+str(summary['matched'])+' of '+str(summary['total'])+''' expectations matched.</strong>
Incomplete evaluations: '''+str(summary['incomplete'])+' · Simulated cases: '+str(summary['simulated'])+'''.</p>
<p>This checks expected behavior on selected examples. A matched expectation can intentionally include a finding or incomplete evaluation. This is not detector accuracy, privacy certification, or a completed safety/bias assessment. Only local engine adapters ran; API authentication, ingress, request limits, and live providers are outside this report. Submitted and redacted text are omitted.</p>
<div class="table"><table><thead><tr>'''+''.join('<th>'+escape(title)+'</th>' for title in titles)+'''</tr></thead><tbody>'''+''.join(rows)+'''</tbody></table></div></html>'''


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run local Valinator regression expectations without provider calls.')
    parser.add_argument('--cases', default='evals/local.json')
    parser.add_argument('--policies', default='policies/playground.json')
    parser.add_argument('--pii',action='store_true',help='Enable the optional offline Presidio adapter')
    parser.add_argument('--group',action='append',help='Select a group; repeat for multiple groups')
    parser.add_argument('--report',help='Write a JSON report without submitted content')
    parser.add_argument('--html',help='Write a standalone HTML report without submitted content')
    parser.add_argument('--overwrite',action='store_true',help='Replace existing report files (never source inputs)')
    args = parser.parse_args(argv)
    try:
        sources = {Path(args.cases).resolve(),Path(args.policies).resolve()}
        targets = [Path(path).resolve() for path in [args.report,args.html] if path]
        source_alias = any(p in sources or (p.exists() and any(s.exists() and p.samefile(s) for s in sources)) for p in targets)
        target_alias = any(a.exists() and b.exists() and a.samefile(b) for i,a in enumerate(targets) for b in targets[i+1:])
        if len(targets) != len(set(targets)) or source_alias or target_alias:
            raise ValueError('Reports must use distinct paths separate from input files')
        if not args.overwrite and any(p.exists() for p in targets):
            raise ValueError('A report already exists; choose a new path or use --overwrite')
        suite = load_suite(args.cases)
        # Deliberately ignore Azure environment settings and never register providers.
        engine = Engine(load_policies(args.policies),default_checkers(enable_pii=args.pii))
        report = asyncio.run(evaluate(suite,engine,set(args.group or [])))
        for name, content in [(args.report,json.dumps(report,indent=2)),(args.html,render_html(report))]:
            if name:
                destination = Path(name)
                destination.parent.mkdir(parents=True,exist_ok=True)
                with destination.open('w' if args.overwrite else 'x',encoding='utf-8') as stream:
                    stream.write(content+'\n')
        summary = report['summary']
        print(f"{summary['matched']}/{summary['total']} expectations matched; {summary['mismatched']} mismatched; {summary['incomplete']} incomplete evaluations; {summary['simulated']} simulated cases.")
        for row in report['cases']:
            if not row['matched']:
                print(row['case_id']+': '+', '.join(row['differences']))
        return 1 if summary['mismatched'] else 0
    except Exception:
        # Validation/provider messages may contain source text; never print them.
        print('Unable to run or save the local regression suite. Check case/policy configuration, optional dependencies, and report paths. Existing reports need --overwrite; input paths cannot be report destinations.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
