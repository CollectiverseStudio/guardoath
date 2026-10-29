"""Provider-neutral safety registration; hosting never selects a provider.

Adapters implement the existing asynchronous Checker contract. The portable
engine receives only this checker ID and normalized results. Vendor SDKs remain
optional and are imported only after an explicit provider selection.
"""

CHECKER_ID = 'content_safety'


def build_content_safety(provider='none'):
    if provider == 'none':
        return None
    if provider != 'bedrock':
        raise ValueError('Unsupported content safety provider; choose none or bedrock')
    from valinator.bedrock_guardrails import from_environment
    adapter = from_environment()

    async def run(request, policy, stage):
        result = await adapter(request, policy, stage)
        return result.model_copy(update={'checker_id': CHECKER_ID})

    run.external = True
    run.provider = provider
    run.checker_version = adapter.checker_version
    return run
