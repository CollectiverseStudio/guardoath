"""Report documented use-case status without equating catalog entries to validators."""
import json
from collections import Counter
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[1] / "docs/use-case-manifest.json"
VALID = {"proposed", "review_required", "partial", "implemented_unverified", "verified"}


def summarize(manifest):
    cases = manifest["cases"]
    if [c["id"] for c in cases] != [f"UC{i:03d}" for i in range(1, 201)]:
        raise ValueError("Expected 200 unique ordered use cases")
    counts = Counter(c["status"] for c in cases)
    if set(counts) - VALID:
        raise ValueError("Unknown status")
    for case in cases:
        if case["status"] == "verified":
            evidence = case.get("verification_evidence") or {}
            if not evidence.get("commit") or not evidence.get("tests"):
                raise ValueError("Verification evidence missing")
    return {"total": len(cases), "verified": counts["verified"],
            "unverified": len(cases) - counts["verified"],
            "by_status": {s: counts[s] for s in sorted(VALID)}}


def main():
    print(json.dumps(summarize(json.loads(MANIFEST.read_text())), indent=2))


if __name__ == "__main__":
    main()
