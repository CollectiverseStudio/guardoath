# Sigma process-creation branch evidence (C11)

**Status:** Offline regression evidence, not a new detector or hosted rule.

`guardoath/sigma_rules/process_branch_evidence.json` contains 12 GuardOath-authored
positive and benign-look-alike event pairs for the three original SigmaHQ
Windows process rules (7-Zip password protection, AccCheckConsole and Auditpol).
It covers alternate image, original filename and command-line selection
branches that were not exercised by the original primary fixtures.

Run `pytest tests/test_sigma_process_branch_evidence.py -q`.

Each fixture is tested against its unchanged original detection, including
missing essential fields and malformed values, while cross-rule tests preserve
real overlaps rather than requiring only one finding. Synthetic evidence can
show local parser behavior; it does **not** establish a false-positive rate,
intent, detection effectiveness or production telemetry compatibility.

**Use cases:** Windows process events can flag potentially concerning
password-protected archive creation, accessibility-verifier invocation with
DLL-loading parameters, and audit policy modification. Legitimate backup,
accessibility testing and administration may exhibit similar behavior and
require surrounding operational context.

**Execution limitations:** These are isolated structured Windows event checks.
They do not run on AI chat messages, do not grant tool permissions, and are
not yet tenant-configured or integrated with the hosted service. Caller-supplied
events and logsource must be authenticated and routed by a trusted host.
Source-revision and licensing provenance remain in the original imported
batch and release integrity manifest. No detection semantics were modified.
