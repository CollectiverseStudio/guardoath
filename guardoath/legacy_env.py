"""Reject legacy configuration names rather than silently dropping safeguards."""
import os


def reject_legacy_environment() -> None:
    replacements = []
    for name in sorted(os.environ):
        for prefix in ("VALINATOR_", "VALISENTRY_"):
            if name.startswith(prefix):
                replacements.append(f"{name} -> GUARDOATH_{name[len(prefix):]}")
                break
    if replacements:
        raise ValueError("Unsupported legacy environment variables: " + ", ".join(replacements))
