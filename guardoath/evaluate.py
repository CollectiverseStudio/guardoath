"""Local-engine regression assertions. Reports exclude prompts and redacted text."""

import argparse
import asyncio
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.compare import load_report, compare_reports, render_section, validate_destinations, write_outputs
from guardoath.json_data import MAX_JSON_BYTES, parse_json
from guardoath.models import Action, CheckRequest, Contract, Stage
from guardoath.synthetic_secrets import PLACEHOLDER, build_fixture

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
    synthetic_fixture: str | None = Field(default=None,pattern=r'^[a-zA-Z0-9_.-]{1,80}$')

    @model_validator(mode='after')
    def valid_fixture(self):
        if self.synthetic_fixture:
            if self.request.content != PLACEHOLDER or self.request.content_format != 'text':
                raise ValueError('Synthetic examples need the explicit text placeholder')
            build_fixture(self.synthetic_fixture)
        return self


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
        content_request=case.request
        if case.synthetic_fixture:
            content_request=CheckRequest.model_validate({**case.request.model_dump(),'content':build_fixture(case.synthetic_fixture)})
        result = await engine.check(content_request,case.stage)
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


def render_html(report, comparison=None):
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
<title>GuardOath regression report</title><style>
body{font:15px system-ui,sans-serif;color:#142135;background:#f6f8fb;margin:32px}
h1{margin-bottom:8px}p{max-width:1000px;line-height:1.5}.table{overflow:auto;background:white;border:1px solid #d8dfeb}
table{border-collapse:collapse;min-width:100%;font-size:13px}th,td{padding:12px;text-align:left;border-bottom:1px solid #dde3ed;vertical-align:top}th{background:#142135;color:white}.bad{background:#fff0ef}.warn{background:#fff7df}
</style><h1>GuardOath regression report</h1><p>Suite: '''+escape(report['suite_id'])+'''</p><p><strong>'''+str(summary['matched'])+' of '+str(summary['total'])+''' expectations matched.</strong>
Incomplete evaluations: '''+str(summary['incomplete'])+' · Simulated cases: '+str(summary['simulated'])+'''.</p>
<p>This checks expected behavior on selected examples. A matched expectation can intentionally include a finding or incomplete evaluation. This is not detector accuracy, privacy certification, or a completed safety/bias assessment. Only local engine adapters ran; API authentication, ingress, request limits, and live providers are outside this report. Submitted and redacted text are omitted.</p>
<div class="table"><table><thead><tr>'''+''.join('<th>'+escape(title)+'</th>' for title in titles)+'''</tr></thead><tbody>'''+''.join(rows)+'''</tbody></table></div>'''+(render_section(comparison) if comparison else '')+'</html>'


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run local GuardOath regression expectations without provider calls.')
    parser.add_argument('--cases', default='evals/local.json')
    parser.add_argument('--policies', default='policies/playground.json')
    parser.add_argument('--pii',action='store_true',help='Enable the optional offline Presidio adapter')
    parser.add_argument('--group',action='append',help='Select a group; repeat for multiple groups')
    parser.add_argument('--report',help='Write a JSON report without submitted content')
    parser.add_argument('--html',help='Write a standalone HTML report without submitted content')
    parser.add_argument('--baseline',help='Compare outcomes with a saved local report of the same suite')
    parser.add_argument('--comparison-report',help='Save the baseline comparison as separate JSON')
    parser.add_argument('--overwrite',action='store_true',help='Replace existing report files (never source inputs)')
    args = parser.parse_args(argv)
    try:
        if args.comparison_report and not args.baseline:
            raise ValueError('A baseline is required for comparison output')
        validate_destinations([args.cases,args.policies,args.baseline],
                              [args.report,args.html,args.comparison_report],args.overwrite)
        suite = load_suite(args.cases)
        baseline = load_report(args.baseline) if args.baseline else None
        if baseline and baseline.suite_id != suite.suite_id:
            raise ValueError('Baseline suite does not match')
        # Deliberately ignore Azure environment settings and never register providers.
        engine = Engine(load_policies(args.policies),default_checkers(enable_pii=args.pii))
        report = asyncio.run(evaluate(suite,engine,set(args.group or [])))
        comparison = compare_reports(baseline,report) if baseline else None
        write_outputs([(args.report,json.dumps(report,indent=2)),(args.html,render_html(report,comparison)),
                       (args.comparison_report,json.dumps(comparison,indent=2))],args.overwrite)
        summary = report['summary']
        print(f"{summary['matched']}/{summary['total']} expectations matched; {summary['mismatched']} mismatched; {summary['incomplete']} incomplete evaluations; {summary['simulated']} simulated cases.")
        if comparison:
            delta = comparison['summary']
            print(f"Baseline: {delta['total'] - delta['unchanged']} changed cases; {delta['new_mismatch']} new mismatches; {delta['removed']} removed cases.")
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
