"""Local citation/quotation consistency against a trusted policy source catalog."""
import re
from pydantic import Field, ValidationError

from guardoath.json_data import parse_json
from guardoath.models import Contract, CheckerResult, Finding

REFERENCE = re.compile(r'\[\[([A-Za-z0-9_.-]{1,80})\]\]')
MESSAGES = {
    'missing_citation': 'The submitted answer needs at least one inline source marker and citation entry.',
    'invalid_marker': 'An inline source marker has unsupported syntax.',
    'unknown_source': 'An inline reference or citation names a source outside the configured catalog.',
    'missing_reference': 'An inline source marker has no corresponding citation entry.',
    'unused_citation': 'A citation entry is not referenced by an inline source marker.',
    'quote_mismatch': 'A submitted quotation is not an exact substring of its configured source.',
    'quote_too_short': 'A submitted quotation is shorter than the configured non-padding character minimum.',
    'duplicate_citation': 'A source has more than one citation entry; this contract permits one quotation per source.',
}


class Citation(Contract):
    source_id: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,80}$', strict=True)
    quote: str = Field(min_length=1, max_length=2_000, strict=True)


class CitedAnswer(Contract):
    answer: str = Field(min_length=1, max_length=8_000, strict=True)
    citations: tuple[Citation, ...] = Field(max_length=20)


async def evidence_check(request, policy, stage):
    def result(status, code=None, findings=()):
        return CheckerResult(checker_id='evidence', checker_version='1', status=status,
                             error_code=code, findings=findings)
    if policy.evidence is None or request.content_format != 'json':
        return result('skipped', 'evidence_json_required')
    try:
        data = CitedAnswer.model_validate(parse_json(request.content))
        if not data.answer.strip() or any(not citation.quote.strip() for citation in data.citations):
            raise ValueError('Blank evidence field')
    except (ValueError, RecursionError, ValidationError):
        return result('error', 'evidence_invalid_document')
    references = REFERENCE.findall(data.answer)
    if len(references) > 20:
        return result('error', 'evidence_reference_limit')
    catalog = {source.source_id: source.text for source in policy.evidence.sources}
    cited = [c.source_id for c in data.citations]
    referenced, cited_set = set(references), set(cited)
    flags = set()
    if not references or not data.citations:
        flags.add('missing_citation')
    stripped = REFERENCE.sub('', data.answer)
    if '[[' in stripped or ']]' in stripped:
        flags.add('invalid_marker')
    if (referenced | cited_set) - catalog.keys():
        flags.add('unknown_source')
    if referenced - cited_set:
        flags.add('missing_reference')
    if cited_set - referenced:
        flags.add('unused_citation')
    if len(cited) != len(cited_set):
        flags.add('duplicate_citation')
    for citation in data.citations:
        if len(citation.quote.strip()) < policy.evidence.min_quote_characters:
            flags.add('quote_too_short')
        if citation.source_id in catalog and citation.quote not in catalog[citation.source_id]:
            flags.add('quote_mismatch')
    findings = tuple(Finding(rule_id='evidence.' + key, category='accuracy', severity='medium',
        explanation=message + ' Citation consistency does not establish factual truth or that a source supports every claim.')
        for key, message in MESSAGES.items() if key in flags)
    return result('flagged' if findings else 'passed', findings=findings)


evidence_check.checker_version = '1'
