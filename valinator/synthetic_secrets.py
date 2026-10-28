"""Explicit synthetic examples for offline regression cases; never real keys.

Values are constructed only when requested. No network, random credentials,
file access, provider verification, imports selected by users, or eval.
"""

import base64
import json

PLACEHOLDER='[SYNTHETIC FIXTURE]'


def jwt_sample():
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    return '.'.join([encode({'alg':'HS256'}),encode({'sub':'synthetic-demo'}),encode('not-a-real-signature')])


def provider_examples():
    return {
        'aws_credentials':'AK'+'IA'+'A'*16,
        'github_token':'ghp'+'_'+'A'*36,
        'jwt':jwt_sample(),
        'openai_token':'sk-'+'A'*20+'T3BlbkFJ'+'B'*20,
        'private_key':'-----BEGIN'+' RSA PRIVATE KEY-----',
        'stripe_key':'sk_'+'live_'+'a'*24,
        'gitlab_token':'glpat-'+'a'*20,
        'slack':'xoxb-'+'1234567890-'+'1234567890-'+'a'*24,
        'sendgrid':'SG.'+'A'*22+'.'+'B'*43,
        'twilio':'SK'+'a'*32,
        'npm':'//registry.npmjs.org/' + ':_authToken=npm_'+'a'*36,
        'pypi':'pypi-'+'AgEIcHlwaS5vcmc'+'A'*70,
        'azure_storage':'AccountKey='+'A'*86+'==',
        'basic_auth':'https://'+'example-user'+':'+'synthetic-pass'+'@example.invalid/path',
        'discord':'M'+'A'*23+'.'+'B'*6+'.'+'C'*27,
        'telegram':'123456789'+':'+'A'*35,
    }


def build_fixture(name):
    parts=name.split('.')
    examples=provider_examples()
    if len(parts)==1 and name in examples:
        return examples[name]
    if len(parts)==2 and parts[0] in {'base64','hex','nested-base64'} and parts[1] in examples:
        raw=examples[parts[1]].encode()
        if parts[0]=='hex': return 'hex: '+raw.hex()
        payload='base64: '+base64.b64encode(raw).decode()
        return payload if parts[0]=='base64' else 'base64: '+base64.b64encode(payload.encode()).decode()
    variants={
        'github_fine':'github_pat_'+'A'*22+'_'+'B'*59,
        'openai_project':'sk-proj-'+'A'*40+'T3BlbkFJ'+'B'*40,
        'private_ec':'-----BEGIN'+' EC PRIVATE KEY-----',
        'private_pgp':'-----BEGIN'+' PGP PRIVATE KEY BLOCK-----',
    }
    if name in variants: return variants[name]
    raise ValueError('Unknown synthetic fixture')
