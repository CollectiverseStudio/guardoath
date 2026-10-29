"""Optional AWS Bedrock text-safety adapter; lazy IAM client, no model invocation."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import os
import re
import threading

from guardoath.models import CheckerResult, Finding, Stage

CHECKER_ID = 'bedrock_guardrails'
VERSION = 'bedrock-apply-guardrail/1'
MAX_CHARACTERS = 10_000  # Deliberately conservative application cap, not AWS quota.
CATEGORIES = {'HATE': 'hate', 'INSULTS': 'insults', 'SEXUAL': 'sexual',
              'VIOLENCE': 'violence', 'MISCONDUCT': 'misconduct'}
LEVELS = {'NONE', 'LOW', 'MEDIUM', 'HIGH'}


def configuration(region, identifier, version):
    # Explicit commercial-AWS region + short resource ID only; no URL/ARN override.
    if not re.fullmatch(r'(?:us|eu|ap|sa|ca|me|af|il|mx)-[a-z]+-[1-9][0-9]?', region):
        raise ValueError('Configure an explicit supported commercial AWS region')
    if not re.fullmatch(r'[a-z0-9]{1,64}', identifier):
        raise ValueError('Configure a short Bedrock guardrail identifier')
    if not re.fullmatch(r'[1-9][0-9]{0,7}', version):
        raise ValueError('Configure a published numeric Bedrock guardrail version; DRAFT is not supported')


def _sdk_call(region, identifier, version, text, stage, timeout):
    import boto3
    from botocore.config import Config
    # IAM role / normal SDK credential chain, never browser keys. Client creation
    # is lazy: enabling startup alone does not query credentials or call AWS.
    config = Config(connect_timeout=1, read_timeout=min(timeout, 5),
                    retries={'total_max_attempts': 1, 'mode': 'standard'},
                    proxies={}, ignore_configured_endpoint_urls=True)
    with closing(boto3.Session(region_name=region).client('bedrock-runtime', region_name=region,
            endpoint_url=f'https://bedrock-runtime.{region}.amazonaws.com',
            verify=True, config=config)) as client:
        return client.apply_guardrail(guardrailIdentifier=identifier, guardrailVersion=version,
            source='INPUT' if stage == Stage.INPUT else 'OUTPUT',
            content=[{'text': {'text': text}}], outputScope='FULL')


def interpret(data, expected_characters):
    """Require all five active filter assessments, including detect-only signals.

    Provider outputs, actionReason, matches and exception strings never escape.
    Configurations with additional policy assessments are intentionally unsupported.
    """
    if not isinstance(data, dict) or data.get('action') not in ('NONE', 'GUARDRAIL_INTERVENED'):
        raise ValueError('Invalid guardrail response')
    coverage = data.get('guardrailCoverage')
    characters = coverage.get('textCharacters') if isinstance(coverage, dict) else None
    if (not isinstance(characters, dict)
            or type(characters.get('guarded')) is not int
            or type(characters.get('total')) is not int
            or characters['guarded'] != expected_characters
            or characters['total'] != expected_characters):
        raise ValueError('Missing or partial text coverage')
    assessments = data.get('assessments')
    if not isinstance(assessments, list) or len(assessments) != 1 or not isinstance(assessments[0], dict):
        raise ValueError('Expected one content assessment')
    assessment = assessments[0]
    if set(assessment) - {'contentPolicy', 'invocationMetrics', 'appliedGuardrailDetails'}:
        raise ValueError('Unsupported guardrail policy assessment')
    content = assessment.get('contentPolicy')
    if not isinstance(content, dict) or not isinstance(content.get('filters'), list) or len(content['filters']) != 5:
        raise ValueError('Expected all five content filters')
    indexed = {}
    for row in content['filters']:
        if not isinstance(row, dict):
            raise ValueError('Invalid content filter')
        kind, confidence, strength, detected, action = (row.get(k) for k in ['type', 'confidence', 'filterStrength', 'detected', 'action'])
        if (not isinstance(kind, str) or kind not in CATEGORIES or kind in indexed
                or not isinstance(confidence, str) or confidence not in LEVELS
                or not isinstance(strength, str) or strength not in LEVELS - {'NONE'}
                or type(detected) is not bool or action not in ('NONE', 'BLOCKED')
                or (action == 'BLOCKED' and (not detected or data['action'] != 'GUARDRAIL_INTERVENED'))):
            raise ValueError('Invalid or inactive content filter')
        indexed[kind] = row
    findings = []
    for kind, name in CATEGORIES.items():
        row = indexed[kind]
        # NONE is not a pass when AWS detect mode reports detected=true.
        if row['detected']:
            findings.append(Finding(rule_id='harmful_content.bedrock_' + name,
                category='harmful_content', severity='medium',
                explanation=f'Amazon Bedrock reported {name} content under the published guardrail settings. Provider confidence: {row["confidence"].lower()}; filter strength: {row["filterStrength"].lower()}. This signal needs application-context review.'))
    if data['action'] == 'GUARDRAIL_INTERVENED' and not findings:
        raise ValueError('Unexplained guardrail intervention')
    return tuple(findings)


def build_bedrock_guardrails(region, identifier, version, *, call=None):
    configuration(region, identifier, version)
    if call is None:
        try:
            import boto3  # noqa: F401 -- verify optional installation, no client yet
        except ImportError:
            raise ValueError('Install the optional aws dependencies before enabling Bedrock Guardrails') from None
        call = _sdk_call
    # A timed-out HTTP request can continue in boto3. Retain its slot until the
    # actual worker ends, preventing timeouts from growing an unbounded queue.
    slots = threading.BoundedSemaphore(2)
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='guardoath-bedrock')

    def result(status, code=None, findings=()):
        return CheckerResult(checker_id=CHECKER_ID, checker_version=VERSION, status=status,
                             error_code=code, external=True, findings=findings)

    async def run(request, policy, stage):
        if request.content_format != 'text':
            return result('unsupported', 'bedrock_text_only')
        if len(request.content) > MAX_CHARACTERS:
            return result('error', 'bedrock_input_limit')
        if not slots.acquire(blocking=False):
            return result('error', 'bedrock_busy')
        try:
            future = executor.submit(call, region, identifier, version, request.content, stage, policy.timeout_ms / 1000)
        except Exception:
            slots.release()
            return result('error', 'bedrock_worker_failure')
        future.add_done_callback(lambda _: slots.release())
        try:
            data = await asyncio.shield(asyncio.wrap_future(future))
            findings = interpret(data, len(request.content))
            return result('flagged' if findings else 'passed', findings=findings)
        except ValueError:
            return result('error', 'bedrock_invalid_response')
        except Exception as exc:
            # Inspect fixed SDK error codes only. Never surface message/body/ARN.
            from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, PartialCredentialsError, ReadTimeoutError, ConnectTimeoutError
            if isinstance(exc, (NoCredentialsError, PartialCredentialsError)):
                code = 'bedrock_credentials_missing'
            elif isinstance(exc, (ReadTimeoutError, ConnectTimeoutError)):
                code = 'bedrock_timeout'
            elif isinstance(exc, ClientError):
                provider_code = exc.response.get('Error', {}).get('Code')
                code = {'AccessDeniedException': 'bedrock_access_denied',
                        'ThrottlingException': 'bedrock_rate_limited',
                        'ServiceQuotaExceededException': 'bedrock_rate_limited',
                        'ResourceNotFoundException': 'bedrock_resource_missing',
                        'ValidationException': 'bedrock_request_rejected'}.get(provider_code, 'bedrock_provider_error')
            else:
                code = 'bedrock_connection_failed' if isinstance(exc, BotoCoreError) else 'bedrock_provider_error'
            return result('error', code)

    run.external = True
    run.checker_version = VERSION
    return run


def from_environment():
    return build_bedrock_guardrails(os.getenv('GUARDOATH_AWS_REGION', ''),
        os.getenv('GUARDOATH_BEDROCK_GUARDRAIL_ID', ''), os.getenv('GUARDOATH_BEDROCK_GUARDRAIL_VERSION', ''))
