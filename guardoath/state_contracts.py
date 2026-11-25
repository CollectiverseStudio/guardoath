"""Persistent-state contracts supported and introduced by this source release.

Changing these declarations requires implementation/acceptance evidence. They
are copied into the Lambda artifact, never supplied by deployment arguments.
An older rollback image must preserve and enforce every introduced contract.
"""
import re

RELEASE_CONTRACT_VERSION = 1
SUPPORTED_STATE_CONTRACTS = ("subscription-cap-provenance-v1", "stripe-period-end-cancellation-v1",
                             "catalog-publication-v1", "catalog-publication-lifecycle-v1")
# First deploy and accept this compatibility source over the legacy image.
# Only a later source commit may enable creation, after the deployed backup
# actually supports the contract. There is deliberately no environment or
# deployment-argument override: it would invalidate the artifact declaration.
ENABLE_NEW_SUBSCRIPTION_STATE = False
# Cancellation is an independent writer phase. Supporting/reading its signed
# durable claims does not authorize either financial dispatch or cap writers.
ENABLE_NEW_CANCELLATION_STATE = False
# Catalog requests and their external source-preparation claims are independent
# persistent state. Deploy the real readers before a later source enables writes.
ENABLE_NEW_CATALOG_PUBLICATION_STATE = False
# Remote publication, accepted CI, deployment and terminal release introduce
# their own signed records. A rollback image must understand this complete
# lifecycle before either catalog writer phase is activated.
ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE = False


def new_subscription_state_enabled() -> bool:
    if type(ENABLE_NEW_SUBSCRIPTION_STATE) is not bool:
        raise ValueError("Subscription release phase must be source controlled")
    return ENABLE_NEW_SUBSCRIPTION_STATE


def new_cancellation_state_enabled() -> bool:
    if type(ENABLE_NEW_CANCELLATION_STATE) is not bool:
        raise ValueError("Cancellation release phase must be source controlled")
    return ENABLE_NEW_CANCELLATION_STATE


def new_catalog_publication_state_enabled() -> bool:
    if type(ENABLE_NEW_CATALOG_PUBLICATION_STATE) is not bool:
        raise ValueError("Catalog publication release phase must be source controlled")
    return ENABLE_NEW_CATALOG_PUBLICATION_STATE


def new_catalog_publication_lifecycle_state_enabled() -> bool:
    if type(ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE) is not bool:
        raise ValueError("Catalog lifecycle release phase must be source controlled")
    return ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE


def release_metadata(source_sha: str) -> dict:
    if not isinstance(source_sha, str) or not re.fullmatch(r"[a-f0-9]{40}", source_sha):
        raise ValueError("An exact committed source SHA is required")
    introduced = []
    if new_subscription_state_enabled():
        introduced.append("subscription-cap-provenance-v1")
    if new_cancellation_state_enabled():
        introduced.append("stripe-period-end-cancellation-v1")
    if new_catalog_publication_state_enabled():
        introduced.append("catalog-publication-v1")
    if new_catalog_publication_lifecycle_state_enabled():
        introduced.append("catalog-publication-lifecycle-v1")
    return {"source_sha":source_sha,"release_contract_version":RELEASE_CONTRACT_VERSION,
            "supported_state_contracts":list(SUPPORTED_STATE_CONTRACTS),
            "introduced_state_contracts":introduced}
