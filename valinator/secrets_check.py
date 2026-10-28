"""Bounded offline checks using selected detect-secrets provider patterns.

Never invokes verification, scans files, creates baselines, or respects allow
comments supplied in untrusted content. Reports categories, not matched values.
"""

import asyncio
from importlib.metadata import version
from time import monotonic

import regex
from detect_secrets.plugins.aws import AWSKeyDetector
from detect_secrets.plugins.github_token import GitHubTokenDetector
from detect_secrets.plugins.jwt import JwtTokenDetector
from detect_secrets.plugins.openai import OpenAIDetector
from detect_secrets.plugins.private_key import PrivateKeyDetector
from detect_secrets.plugins.stripe import StripeDetector

from valinator.json_data import parse_json
from valinator.models import CheckerResult, Finding


def build_secret_checker():
    detectors = (
        ("aws_credentials", AWSKeyDetector(), None),
        ("github_token", GitHubTokenDetector(), None),
        ("jwt", JwtTokenDetector(), JwtTokenDetector.is_formally_valid),
        ("openai_token", OpenAIDetector(), None),
        ("private_key", PrivateKeyDetector(), None),
        ("stripe_key", StripeDetector(), None),
    )
    # Reuse upstream expressions, but propagate regex timeout rather than treating
    # an uncompleted scan as a pass. No .verify() or global plugin settings used.
    patterns = tuple((name, regex.compile(pattern.pattern, pattern.flags), validate)
                     for name, detector, validate in detectors for pattern in detector.denylist)
    checker_version = f"detect-secrets-{version('detect-secrets')}/valinator-patterns-1"

    def texts(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from texts(item)
        elif isinstance(value, list):
            for item in value:
                yield from texts(item)

    def scan(request):
        deadline = monotonic() + .2
        parts = (request.content,) if request.content_format == "text" else texts(parse_json(request.content))
        found = set()
        for text in parts:
            for name, pattern, validate in patterns:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("Local secret scan deadline")
                for match in pattern.finditer(text, timeout=remaining):
                    if monotonic() >= deadline:
                        raise TimeoutError("Local secret scan deadline")
                    if validate is None or validate(match.group()):
                        found.add(name)
        return found

    async def run(request, policy, stage):
        try:
            found = await asyncio.to_thread(scan, request)
        except (ValueError, RecursionError):
            return CheckerResult(checker_id="secrets", checker_version=checker_version,
                                 status="error", error_code="invalid_json_for_secrets")
        names = {"aws_credentials":"AWS credential or access-key identifier pattern",
                 "github_token":"supported GitHub token pattern", "jwt":"formally structured JSON Web Token",
                 "openai_token":"supported OpenAI token pattern", "private_key":"private-key marker",
                 "stripe_key":"supported Stripe secret-key pattern"}
        findings = tuple(Finding(rule_id=f"secrets.{name}", category="secrets", severity="high",
                         explanation=f"Potential {names[name]} detected locally. Credential activity, ownership, and cryptographic validity were not verified.")
                         for name in sorted(found))
        return CheckerResult(checker_id="secrets", checker_version=checker_version,
                             status="flagged" if findings else "passed", findings=findings)

    setattr(run, "checker_version", checker_version)
    return run
