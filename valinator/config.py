"""Load trusted, versioned policy files. No live policy edits or remote schemas."""

import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from valinator.models import Contract, Policy


class PolicyBundle(Contract):
    format_version: Literal["1"]
    policies: tuple[Policy, ...] = Field(min_length=1, max_length=100)


def validate_local_schema(schema):
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key in {"$ref", "$dynamicRef"} and isinstance(value, str) and not value.startswith("#"):
                raise ValueError("Only local schema references are supported")
            validate_local_schema(value)
    elif isinstance(schema, list):
        for value in schema:
            validate_local_schema(value)


def load_policies(path: str | Path) -> list[Policy]:
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError("Policy file too large")
        bundle = PolicyBundle.model_validate_json(raw)
        return list(bundle.policies)
    except Exception:
        # Do not expose config contents, file paths, or embedded credentials.
        raise ValueError("Unable to load policy configuration") from None
