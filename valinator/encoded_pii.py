"""One labeled encoding layer, reusing the optional local PII adapter."""
from valinator.json_data import parse_json
from valinator.models import Action, CheckerResult, Finding
from valinator.text_security import ENCODED, MAX_PAYLOADS, MAX_TOKEN_CHARACTERS, MAX_TOTAL_TOKEN_CHARACTERS, EncodingIssue, _decode, texts


def build_encoded_pii_checker(pii_checker):
    version = getattr(pii_checker, 'checker_version', 'unknown') + '/encoded-1'

    async def run(request, policy, stage):
        def result(status, code=None, findings=()):
            return CheckerResult(checker_id='encoded_pii', checker_version=version,
                                 status=status, error_code=code, findings=findings)
        # This checker never transforms encoded values. Raw PII redaction is a
        # separate operation, whose all-check revalidation must still succeed.
        scan_policy = policy.model_copy(update={'rules': tuple(rule.model_copy(update={
            'on_finding': Action.FLAG, 'on_input_finding': Action.FLAG, 'on_output_finding': Action.FLAG
        }) if rule.checker_id == 'pii' else rule for rule in policy.rules)})
        findings = {}
        count = total = 0
        try:
            parts = (request.content,) if request.content_format == 'text' else texts(parse_json(request.content))
            for part in parts:
                for match in ENCODED.finditer(part):
                    kind, token = match.groups()
                    count += 1
                    total += len(token)
                    if count > MAX_PAYLOADS or len(token) > MAX_TOKEN_CHARACTERS or total > MAX_TOTAL_TOKEN_CHARACTERS:
                        return result('error', 'encoded_pii_budget_exceeded')
                    try:
                        decoded = _decode(kind, token)
                    except EncodingIssue:
                        return result('error', 'encoded_pii_invalid_encoding')
                    # Structured payloads are scanned as JSON, preserving decoded
                    # keys/escapes/field context. Malformed structured text is not
                    # silently downgraded into a completed plain-text scan.
                    structured = decoded.lstrip().startswith(('{', '[', '"'))
                    if structured:
                        try:
                            inner_parts = texts(parse_json(decoded))
                            nested = any(ENCODED.search(value) for value in inner_parts)
                        except (ValueError, RecursionError):
                            return result('error', 'encoded_pii_invalid_decoded_json')
                    else:
                        nested = bool(ENCODED.search(decoded))
                    if nested:
                        return result('error', 'encoded_pii_nested_payload')
                    scan_request = request.model_copy(update={'content':decoded,
                        'content_format':'json' if structured else 'text', 'request_context':None})
                    scanned = await pii_checker(scan_request, scan_policy, stage)
                    if (scanned.checker_id != 'pii' or scanned.status not in {'passed', 'flagged'}
                            or (scanned.status == 'flagged') != bool(scanned.findings)
                            or scanned.simulated or scanned.external):
                        return result('error', 'encoded_pii_scan_incomplete')
                    for finding in scanned.findings:
                        rule_id = 'pii.encoded_' + finding.rule_id.removeprefix('pii.')
                        findings[rule_id] = Finding(rule_id=rule_id, category='privacy', severity=finding.severity,
                            explanation='Supported sensitive-data candidate found inside a labeled encoded payload. '
                                + finding.explanation + ' Encoded content was not modified; decoded text is not returned.')
        except (ValueError, RecursionError):
            return result('error', 'encoded_pii_invalid_json')
        except TimeoutError:
            return result('error', 'encoded_pii_scan_incomplete')
        return result('flagged' if findings else 'passed', findings=tuple(findings[k] for k in sorted(findings)))

    run.checker_version = version
    return run
