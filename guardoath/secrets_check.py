"""Bounded local credential and configured confidential-term checks.

No verification, network, source echoes, suppression comments, or file scanning.
"""

import asyncio
import base64
import binascii
from importlib.metadata import version
from time import monotonic
import unicodedata

import regex
from detect_secrets.plugins.aws import AWSKeyDetector
from detect_secrets.plugins.github_token import GitHubTokenDetector
from detect_secrets.plugins.jwt import JwtTokenDetector
from detect_secrets.plugins.openai import OpenAIDetector
from detect_secrets.plugins.private_key import PrivateKeyDetector
from detect_secrets.plugins.stripe import StripeDetector
from detect_secrets.plugins.gitlab_token import GitLabTokenDetector
from detect_secrets.plugins.slack import SlackDetector
from detect_secrets.plugins.sendgrid import SendGridDetector
from detect_secrets.plugins.twilio import TwilioKeyDetector
from detect_secrets.plugins.npm import NpmDetector
from detect_secrets.plugins.pypi_token import PypiTokenDetector
from detect_secrets.plugins.azure_storage_key import AzureStorageKeyDetector
from detect_secrets.plugins.basic_auth import BasicAuthDetector
from detect_secrets.plugins.discord import DiscordBotTokenDetector
from detect_secrets.plugins.telegram_token import TelegramBotTokenDetector

from guardoath.json_data import parse_json
from guardoath.models import CheckerResult, Finding
from guardoath.text_security import ENCODED, MAX_PAYLOADS, MAX_TOKEN_CHARACTERS, MAX_TOTAL_TOKEN_CHARACTERS

# Deliberately limited literal-assignment heuristic, not a code parser.
VARIABLE = r'(?:[a-z0-9]{1,32}_){0,3}(?:password|passwd|pwd|api_key|auth_token|access_token|refresh_token|client_secret|secret_key|secret|token)'
FIELD = regex.compile(r'\A'+VARIABLE+r'\Z',regex.IGNORECASE)
ASSIGNMENT = regex.compile(r'(?<![a-z0-9_])'+VARIABLE+r'''["']?\s*[:=]\s*(?P<quote>["'])(?P<value>[^"'\r\n]*)(?P=quote)''',regex.IGNORECASE)
REFERENCE = regex.compile(r'\A(?:\$\{[a-z_][a-z0-9_]*\}|\{\{\s*[a-z_][a-z0-9_.]*\s*\}\}|<[a-z_][a-z0-9_ -]*>|YOUR_[A-Z0-9_]+)\Z',regex.IGNORECASE)


def normalize(value):
    return unicodedata.normalize('NFKC',value).casefold()


class ScanLimit(ValueError):
    pass


def units(value):
    if isinstance(value,str):
        yield value,False
    elif isinstance(value,dict):
        for key,item in value.items():
            yield key,False
            if isinstance(item,str):
                yield item,bool(FIELD.fullmatch(key))
            else:
                yield from units(item)
    elif isinstance(value,list):
        for item in value:
            yield from units(item)


def build_secret_checker():
    detectors = (
        ('aws_credentials',AWSKeyDetector(),None),('github_token',GitHubTokenDetector(),None),
        ('jwt',JwtTokenDetector(),JwtTokenDetector.is_formally_valid),('openai_token',OpenAIDetector(),None),
        ('private_key',PrivateKeyDetector(),None),('stripe_key',StripeDetector(),None),
        ('gitlab_token',GitLabTokenDetector(),None),('slack',SlackDetector(),None),
        ('sendgrid',SendGridDetector(),None),('twilio',TwilioKeyDetector(),None),
        ('npm',NpmDetector(),None),('pypi',PypiTokenDetector(),None),
        ('azure_storage',AzureStorageKeyDetector(),None),('basic_auth',BasicAuthDetector(),None),
        ('discord',DiscordBotTokenDetector(),None),('telegram',TelegramBotTokenDetector(),None),
    )
    patterns = [(name,regex.compile(p.pattern,p.flags),validate)
                for name,detector,validate in detectors for p in detector.denylist]
    # Prefix-based candidate coverage; not GitHub credential validity verification.
    patterns.append(('github_token',regex.compile(r'github_pat_[A-Za-z0-9_]{20,255}'),None))
    checker_version=f'detect-secrets-{version("detect-secrets")}/guardoath-patterns-2'

    def scan(request,policy,stage):
        deadline=monotonic()+.2
        parts=((request.content,False),) if request.content_format=='text' else units(parse_json(request.content))
        found=set()
        terms=[(rule.rule_id,normalize(rule.term)) for rule in policy.confidential_terms if stage in rule.stages]
        count=total=0
        def remaining():
            value=deadline-monotonic()
            if value<=0: raise TimeoutError('Local secret scan deadline')
            return value
        def assignment(value):
            if len(value)>512: raise ScanLimit('secret_assignment_limit')
            if value.strip() and not REFERENCE.fullmatch(value.strip(),timeout=remaining()):
                found.add('credential_assignment')
        def inspect(text,field=False):
            for name,pattern,validate in patterns:
                for match in pattern.finditer(text,timeout=remaining()):
                    remaining()
                    if validate is None or validate(match.group()): found.add(name)
            if policy.detect_secret_assignments:
                if field: assignment(text)
                for match in ASSIGNMENT.finditer(text,timeout=remaining()):
                    assignment(match['value'])
            if terms:
                normalized=normalize(text)
                for rule_id,term in terms:
                    remaining()
                    if term in normalized: found.add('confidential.'+rule_id)
        for text,field in parts:
            inspect(text,field)
            if policy.inspect_encoded_secrets:
                for match in ENCODED.finditer(text):
                    remaining()
                    kind,token=match.groups()
                    count+=1; total+=len(token)
                    if count>MAX_PAYLOADS or len(token)>MAX_TOKEN_CHARACTERS or total>MAX_TOTAL_TOKEN_CHARACTERS:
                        raise ScanLimit('secret_encoding_limit')
                    try:
                        if not token: raise ValueError('Empty payload')
                        if kind.lower()=='base64':
                            decoded=base64.b64decode(token,validate=True).decode('utf-8')
                        else:
                            if not regex.fullmatch(r'(?:[a-fA-F0-9]{2})+',token,timeout=remaining()): raise ValueError('Invalid hex')
                            decoded=bytes.fromhex(token).decode('utf-8')
                    except (ValueError,UnicodeError,binascii.Error):
                        raise ScanLimit('secret_encoding_invalid') from None
                    inspect(decoded)  # Exactly one layer, never recursively decode.
        remaining()
        return found

    async def run(request,policy,stage):
        try:
            found=await asyncio.to_thread(scan,request,policy,stage)
        except ScanLimit as exc:
            return CheckerResult(checker_id='secrets',checker_version=checker_version,status='error',error_code=str(exc))
        except (ValueError,RecursionError):
            return CheckerResult(checker_id='secrets',checker_version=checker_version,status='error',error_code='invalid_json_for_secrets')
        names={'aws_credentials':'AWS credential or access-key identifier','github_token':'supported GitHub token',
            'jwt':'formally structured JSON Web Token','openai_token':'supported OpenAI token',
            'private_key':'private-key marker','stripe_key':'supported Stripe secret-key',
            'gitlab_token':'GitLab token','slack':'Slack token/webhook','sendgrid':'SendGrid token',
            'twilio':'Twilio credential identifier','npm':'npm registry assignment','pypi':'PyPI token',
            'azure_storage':'Azure Storage account-key','basic_auth':'URL with embedded credentials',
            'discord':'supported Discord token','telegram':'Telegram bot-token',
            'credential_assignment':'quoted credential assignment'}
        findings=[]
        for name in sorted(found):
            if name.startswith('confidential.'):
                rule_id=name
                explanation='A configured confidential-term rule matched. This is literal matching, not a general trade-secret or proprietary-document assessment.'
            else:
                rule_id='secrets.'+name
                explanation=f'Potential {names[name]} pattern detected locally. Credential activity, ownership, and cryptographic validity were not verified.'
            findings.append(Finding(rule_id=rule_id,category='secrets',severity='high',explanation=explanation))
        return CheckerResult(checker_id='secrets',checker_version=checker_version,status='flagged' if findings else 'passed',findings=tuple(findings))
    run.checker_version=checker_version
    return run
