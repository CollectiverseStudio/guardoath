"""Apply an opt-in lexical checker registration to a local checkout.

Run from repository root. Does not modify policies or deploy.
"""
from pathlib import Path
p = Path("guardoath/checkers.py")
source = p.read_text(encoding="utf-8")
import_line = "from guardoath.lexical_checker import lexical_guards_check\n"
anchor = "from guardoath.models import CheckerResult, CheckRequest, Finding, Policy, Stage\n"
if import_line not in source:
    if source.count(anchor) != 1:
        raise SystemExit("Unexpected checkers.py import layout; review manually")
    source = source.replace(anchor, anchor + import_line)
needle = '"integrity": integrity_check}'
replacement = '"integrity": integrity_check, "lexical_guards": lexical_guards_check}'
if needle in source:
    source = source.replace(needle, replacement, 1)
elif replacement not in source:
    raise SystemExit("Unexpected checker registry layout; review manually")
p.write_text(source, encoding="utf-8")
print('Registered lexical_guards. Configure Rule(checker_id="lexical_guards", on_finding="block") and mode="enforce" separately.')
