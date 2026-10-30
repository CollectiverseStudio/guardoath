"""Provenance envelope for untrusted RAG and tool-response text.

The envelope preserves trust metadata but does not sanitize content, classify
injection, or authorize an LLM to consume it.
"""
from dataclasses import dataclass
from enum import StrEnum


class Surface(StrEnum):
    USER_INPUT = "user_input"
    RETRIEVED_DOCUMENT = "retrieved_document"
    TOOL_RESPONSE = "tool_response"


@dataclass(frozen=True)
class UntrustedContent:
    tenant_id: str
    application_id: str
    source_id: str
    surface: Surface
    text: str

    def __post_init__(self):
        if not self.tenant_id or not self.application_id or not self.source_id:
            raise ValueError("Trusted provenance identifiers required")
        if not isinstance(self.text, str) or len(self.text.encode("utf-8")) > 1024 * 1024:
            raise ValueError("Untrusted content exceeds maximum size")

    def assessment_metadata(self) -> dict[str, str]:
        return {
            "tenant_id": self.tenant_id,
            "application_id": self.application_id,
            "source_id": self.source_id,
            "surface": self.surface.value,
        }
