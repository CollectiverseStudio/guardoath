"""Persistent-state contracts supported and introduced by this source release.

Changing these declarations requires implementation/acceptance evidence. They
are copied into the Lambda artifact, never supplied by deployment arguments.
An older rollback image must preserve and enforce every introduced contract.
"""
import re

RELEASE_CONTRACT_VERSION = 1
SUPPORTED_STATE_CONTRACTS = ("subscription-cap-provenance-v1",)
# First deploy and accept this compatibility source over the legacy image.
# Only a later source commit may enable creation, after the deployed backup
# actually supports the contract. There is deliberately no environment or
# deployment-argument override: it would invalidate the artifact declaration.
ENABLE_NEW_SUBSCRIPTION_STATE = False


def new_subscription_state_enabled() -> bool:
    if type(ENABLE_NEW_SUBSCRIPTION_STATE) is not bool:
        raise ValueError("Subscription release phase must be source controlled")
    return ENABLE_NEW_SUBSCRIPTION_STATE


def release_metadata(source_sha: str) -> dict:
    if not isinstance(source_sha, str) or not re.fullmatch(r"[a-f0-9]{40}", source_sha):
        raise ValueError("An exact committed source SHA is required")
    return {"source_sha":source_sha,"release_contract_version":RELEASE_CONTRACT_VERSION,
            "supported_state_contracts":list(SUPPORTED_STATE_CONTRACTS),
            "introduced_state_contracts":list(SUPPORTED_STATE_CONTRACTS) if new_subscription_state_enabled() else []}
