"""Compare bounded local regression reports without loading their source inputs."""

import argparse
from datetime import datetime
from html import escape
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictBool, field_validator, model_validator

from guardoath.json_data import MAX_JSON_BYTES, parse_json
from guardoath.models import Action, Contract, Stage

Identifier = Annotated[str, Field(pattern=r'^[a-zA-Z0-9_.-]{1,100}$')]
Status = Literal['passed','flagged','error','skipped','unsupported']
OutcomeField = Literal['evaluation_status','recommended_action','effective_action','checker_statuses','finding_rule_ids','redaction_status','simulated']
OUTCOME_FIELDS = tuple(OutcomeField.__args__)


class ReportCase(Contract):
    case_id: Identifier
    group: Identifier
    stage: Stage
    matched: StrictBool
    differences: tuple[OutcomeField, ...] = Field(max_length=7)
    evaluation_status: Literal['complete','incomplete']
    recommended_action: Action
    effective_action: Action
    checker_statuses: dict[Identifier, Status] = Field(min_length=1, max_length=50)
    finding_rule_ids: tuple[Identifier, ...] = Field(max_length=100)
    redaction_status: Literal['not_requested','not_needed','preview','ready','withheld']
    simulated: StrictBool
    latency_ms: float = Field(ge=0, allow_inf_nan=False)

    @field_validator('finding_rule_ids','differences')
    @classmethod
    def canonical_ids(cls,value):
        if len(set(value)) != len(value):
            raise ValueError('Duplicate result identifiers')
        return tuple(sorted(value))

    @model_validator(mode='after')
    def consistent(self):
        if self.matched != (not self.differences) or len(set(self.differences)) != len(self.differences):
            raise ValueError('Inconsistent expectation metadata')
        return self


class Summary(Contract):
    total: int = Field(ge=1,le=100,strict=True)
    matched: int = Field(ge=0,le=100,strict=True)
    mismatched: int = Field(ge=0,le=100,strict=True)
    incomplete: int = Field(ge=0,le=100,strict=True)
    simulated: int = Field(ge=0,le=100,strict=True)


class Report(Contract):
    format_version: Literal['1']
    suite_id: Identifier
    execution_scope: Literal['local_engine']
    created_at: datetime
    summary: Summary
    cases: tuple[ReportCase, ...] = Field(min_length=1,max_length=100)

    @model_validator(mode='after')
    def consistent(self):
        expected = {'total':len(self.cases),'matched':sum(c.matched for c in self.cases),
                    'mismatched':sum(not c.matched for c in self.cases),
                    'incomplete':sum(c.evaluation_status == 'incomplete' for c in self.cases),
                    'simulated':sum(c.simulated for c in self.cases)}
        if self.created_at.tzinfo is None or self.summary.model_dump() != expected or len({c.case_id for c in self.cases}) != len(self.cases):
            raise ValueError('Inconsistent regression report')
        return self


def load_report(path):
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise ValueError('Report too large')
        return Report.model_validate(parse_json(raw.decode('utf-8')))
    except Exception:
        raise ValueError('Unable to load a bounded local regression report') from None


def compare_reports(baseline, current):
    before = baseline if isinstance(baseline,Report) else Report.model_validate(baseline)
    after = current if isinstance(current,Report) else Report.model_validate(current)
    if before.suite_id != after.suite_id:
        raise ValueError('Comparison requires the same suite identifier')
    old = {c.case_id:c.model_dump(mode='json') for c in before.cases}
    new = {c.case_id:c.model_dump(mode='json') for c in after.cases}
    rows = []
    kinds = ['unchanged','new_mismatch','resolved_mismatch','outcome_changed','mismatch_details_changed','identity_changed','added','removed']
    counts = {kind:0 for kind in kinds}
    for case_id in sorted(old.keys() | new.keys()):
        left,right = old.get(case_id),new.get(case_id)
        changed = []
        delta = None
        if left is None:
            kind = 'added'
        elif right is None:
            kind = 'removed'
        else:
            changed = [field for field in ('group','stage',*OUTCOME_FIELDS,'matched','differences') if left[field] != right[field]]
            delta = round(right['latency_ms'] - left['latency_ms'],3)
            if any(field in changed for field in ('group','stage')):
                kind = 'identity_changed'
            elif left['matched'] and not right['matched']:
                kind = 'new_mismatch'
            elif not left['matched'] and right['matched']:
                kind = 'resolved_mismatch'
            elif any(field in changed for field in OUTCOME_FIELDS):
                kind = 'outcome_changed'
            elif changed:
                kind = 'mismatch_details_changed'
            else:
                kind = 'unchanged'
        counts[kind] += 1
        rows.append({'case_id':case_id,'change':kind,'changed_fields':changed,
                     'latency_delta_ms':delta,'baseline':left,'current':right})
    return {'format_version':'1','suite_id':after.suite_id,'execution_scope':'local_engine',
            'baseline_created_at':before.created_at.isoformat(),'current_created_at':after.created_at.isoformat(),
            'summary':{'total':len(rows),**counts,'current_mismatched':after.summary.mismatched},'cases':rows}


LABELS = {'unchanged':'Unchanged','new_mismatch':'New mismatch','resolved_mismatch':'Mismatch resolved',
          'outcome_changed':'Outcome changed','mismatch_details_changed':'Mismatch details changed',
          'identity_changed':'Group/stage changed','added':'Added case','removed':'Removed case'}


def describe(row):
    if row is None:
        return 'Not present'
    return ('Matched' if row['matched'] else 'MISMATCH')+' · '+row['evaluation_status']+' · recommended '+row['recommended_action']+' · effective '+row['effective_action']+' · simulated '+str(row['simulated']).lower()+' · redaction '+row['redaction_status']+' · findings '+(', '.join(row['finding_rule_ids']) or 'none')+' · checkers '+', '.join(k+': '+v for k,v in row['checker_statuses'].items())


def render_section(comparison):
    summary = comparison['summary']
    counts = ' · '.join(LABELS[k]+': '+str(summary[k]) for k in LABELS)
    rows = []
    for row in comparison['cases']:
        cells = [row['case_id'],LABELS[row['change']],', '.join(row['changed_fields']) or '—',
                 describe(row['baseline']),describe(row['current']),
                 '—' if row['latency_delta_ms'] is None else f"{row['latency_delta_ms']:+.3f}"]
        css = 'bad' if row['change']=='new_mismatch' else 'warn' if row['change'] not in {'unchanged','resolved_mismatch'} else ''
        rows.append('<tr class="'+css+'">'+''.join('<td>'+escape(str(cell))+'</td>' for cell in cells)+'</tr>')
    return '<section id="baseline-comparison"><h2>Changes from baseline</h2><p>'+escape(counts)+'</p><p>Comparison uses saved outcome metadata and matching case IDs. Input and expectation definitions are not stored here, so their equality is not verified. Keep case IDs stable and change the suite ID when fixture definitions change. Changed outcomes are not automatically regressions or improvements. Timing deltas do not change outcome classifications. Removed cases are visible coverage changes, not passing cases.</p><div class="table"><table><thead><tr>'+''.join('<th>'+title+'</th>' for title in ['Case','Change','Changed fields','Baseline outcome','Current outcome','Timing delta (ms)'])+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table></div></section>'


def render_html(comparison):
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>GuardOath baseline comparison</title><style>body{font:15px system-ui,sans-serif;color:#142135;background:#f6f8fb;margin:32px}p{line-height:1.5;max-width:1100px}.table{overflow:auto;background:white}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:12px;text-align:left;border-bottom:1px solid #d8dfeb;vertical-align:top}th{background:#142135;color:white}.bad{background:#fff0ef}.warn{background:#fff7df}</style><h1>GuardOath baseline comparison</h1><p>Suite: '''+escape(comparison['suite_id'])+'</p><p>Local regression assertions only. No source inputs or redacted text are included. This is not detector accuracy, a privacy certification, or a completed safety assessment.</p>'+render_section(comparison)+'</html>'


def validate_destinations(sources,targets,overwrite=False):
    sources = {Path(p).resolve() for p in sources if p}
    targets = [Path(p).resolve() for p in targets if p]
    aliases_source = any(p in sources or (p.exists() and any(s.exists() and p.samefile(s) for s in sources)) for p in targets)
    aliases_target = any(a.exists() and b.exists() and a.samefile(b) for i,a in enumerate(targets) for b in targets[i+1:])
    if len(targets) != len(set(targets)) or aliases_source or aliases_target or (not overwrite and any(p.exists() for p in targets)):
        raise ValueError('Report paths must be distinct from protected inputs and existing files')


def write_outputs(outputs,overwrite=False):
    for name,content in outputs:
        if name:
            destination = Path(name)
            destination.parent.mkdir(parents=True,exist_ok=True)
            with destination.open('w' if overwrite else 'x',encoding='utf-8') as stream:
                stream.write(content+'\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Compare local regression result metadata without source inputs.')
    parser.add_argument('--baseline',required=True)
    parser.add_argument('--current',required=True)
    parser.add_argument('--report')
    parser.add_argument('--html')
    parser.add_argument('--overwrite',action='store_true')
    parser.add_argument('--fail-on-change',action='store_true',help='Also fail for any case/outcome/coverage change, excluding timing alone')
    args = parser.parse_args(argv)
    try:
        validate_destinations([args.baseline,args.current],[args.report,args.html],args.overwrite)
        comparison = compare_reports(load_report(args.baseline),load_report(args.current))
        write_outputs([(args.report,json.dumps(comparison,indent=2)),(args.html,render_html(comparison))],args.overwrite)
        summary = comparison['summary']
        changed = summary['total'] - summary['unchanged']
        print(f"{changed} changed cases; {summary['new_mismatch']} new mismatches; {summary['removed']} removed cases; {summary['current_mismatched']} current mismatches.")
        return 1 if summary['current_mismatched'] or (args.fail_on_change and changed) else 0
    except Exception:
        print('Unable to compare local reports. Check their bounded format, unique case IDs, consistent summaries, matching suite IDs, and distinct output paths. Existing outputs need --overwrite.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
