"""Explicitly enabled Azure input-only prompt-attack analysis. No retries."""

import os
import re
from urllib.parse import urlsplit

from valinator.json_data import parse_json
from valinator.models import CheckerResult, Finding, Stage

CHECKER_ID = 'azure_prompt_shields'
VERSION = 'azure-prompt-shields/2024-09-01'
MAX_CHARACTERS = 4_000  # Conservative local cap; never silently truncate or split.
MAX_RESPONSE_BYTES = 16_384


def validate_endpoint(endpoint):
    try:
        parsed = urlsplit(endpoint)
        valid = (parsed.scheme == 'https' and parsed.port in {None, 443}
                 and parsed.username is None and parsed.password is None
                 and parsed.path in {'', '/'} and not parsed.query and not parsed.fragment
                 and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}\.cognitiveservices\.azure\.com', parsed.hostname or ''))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('Azure endpoint must be an HTTPS public-Azure resource origin')
    return f'https://{parsed.hostname}'


def build_azure_prompt_shields(endpoint, key, *, transport=None):
    endpoint = validate_endpoint(endpoint)
    if not key or len(key) > 512 or not key.isascii() or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in key):
        raise ValueError('Azure subscription key is missing or invalid')
    try:
        import httpx2
    except ImportError:
        raise ValueError('Install the optional azure dependencies before enabling Prompt Shields') from None

    def result(status, *, error_code=None, flagged=False):
        return CheckerResult(checker_id=CHECKER_ID, checker_version=VERSION, status=status,
            external=True, error_code=error_code, findings=(Finding(
                rule_id='prompt_security.azure_user_attack', category='prompt_security', severity='high',
                explanation='Azure Prompt Shields reported a possible user-prompt attack. This is a provider signal requiring application-context review, not proof of malicious intent.',
            ),) if flagged else ())

    async def run(request, policy, stage):
        if stage != Stage.INPUT:
            return result('unsupported', error_code='azure_input_only')
        if request.content_format != 'text':
            return result('unsupported', error_code='azure_text_only')
        if len(request.content) > MAX_CHARACTERS:
            return result('error', error_code='azure_input_limit')
        timeout = min(policy.timeout_ms / 1000, 5.0)
        try:
            async with httpx2.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False,
                                          transport=transport) as client:
                async with client.stream('POST', endpoint + '/contentsafety/text:shieldPrompt',
                    params={'api-version':'2024-09-01'},
                    headers={'Ocp-Apim-Subscription-Key':key, 'Accept-Encoding':'identity'},
                    json={'userPrompt':request.content, 'documents':[]}) as response:
                    if response.status_code == 429:
                        return result('error', error_code='azure_rate_limited')
                    if response.status_code in {401, 403}:
                        return result('error', error_code='azure_authorization_failed')
                    if response.status_code != 200:
                        return result('error', error_code='azure_provider_error')
                    if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                        return result('error', error_code='azure_invalid_response')
                    size = response.headers.get('content-length')
                    if size is not None and (not size.isascii() or not size.isdecimal() or len(size) > 8 or int(size) > MAX_RESPONSE_BYTES):
                        return result('error', error_code='azure_invalid_response')
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                            return result('error', error_code='azure_invalid_response')
                        body.extend(chunk)
                    data = parse_json(body.decode('utf-8'))
                    attack = data['userPromptAnalysis']['attackDetected']
                    if type(attack) is not bool or data.get('documentsAnalysis', []) != []:
                        return result('error', error_code='azure_invalid_response')
                    return result('flagged' if attack else 'passed', flagged=attack)
        except httpx2.TimeoutException:
            return result('error', error_code='azure_timeout')
        except httpx2.RequestError:
            return result('error', error_code='azure_connection_failed')
        except (ValueError, UnicodeError, KeyError, TypeError, RecursionError):
            return result('error', error_code='azure_invalid_response')

    run.checker_version = VERSION
    run.external = True
    return run


def from_environment():
    return build_azure_prompt_shields(os.getenv('VALINATOR_AZURE_CONTENT_SAFETY_ENDPOINT', ''),
                                     os.getenv('VALINATOR_AZURE_CONTENT_SAFETY_KEY', ''))
