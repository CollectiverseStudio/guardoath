# Trusted installed Sigma acceptance

`guardoath.sigma_rules.verify_installation` turns the manual installed-wheel smoke
check into an offline command. It executes only the current, explicitly trusted
installation; it does not install packages, fetch rules, invoke outside AI, inspect
or execute an untrusted wheel, or authorize deployment.

First inspect the newly built artifact with `verify_distribution`. Then install
that trusted wheel in a fresh environment using local files only, outside the
checkout. On POSIX, for example:

```sh
python -m venv /tmp/guardoath-acceptance
/tmp/guardoath-acceptance/bin/python -m pip install --no-index --no-deps /absolute/path/guardoath.whl
cd /tmp
/tmp/guardoath-acceptance/bin/python -I -m guardoath.sigma_rules.verify_installation --expected-root /tmp/guardoath-acceptance/lib/python3.13/site-packages
```

Use the actual environment's Python minor version/site-packages path (Windows
uses `Scripts/python.exe` and `Lib/site-packages`). `--expected-root` means the
installation root containing `guardoath/` and its `.dist-info`, not a source
checkout or an enclosing parent. The command checks exact loaded Sigma module
locations, distribution metadata location, non-editable installation, regular
module/asset paths and installed file records. A broad ancestor root does not
authorize imports from another location. Missing/foreign origins fail before
fixture execution. Run with `-I` to exclude current-directory/PYTHONPATH fallback.

It then checks the ten reviewed release fingerprints, all 228 original snapshot
digests and the retained license. For each original rule it verifies the trusted
source routing preview, exact positive finding attribution, benign and empty-event
outcomes, and explicit malformed-event rejection. Each fixture is a separate
event with the existing bounded event evaluator; no event budget is reset or
split to evade limits. No new rule or hosted registration is introduced.

Success emits one JSON record: distribution version, **228 rules / 228 pinned /
239 required assets**, 228 each of positive, benign, missing-event,
malformed-rejection and routing-preview checks, and immutable source revisions.
Exit codes are 0 for complete acceptance, 1 for failure and 2 for usage. Failure
emits a bounded generic diagnostic to stderr, no partial success JSON, event
contents, user-supplied paths or upstream descriptions.

This proves the observed trusted installation's layout and authored fixture
behavior. It is not a signature/authenticity check, protection against replacing
trusted verifier code, an exhaustive branch test or effectiveness measurement.
Unit layout fixtures mock origin discovery and are explicitly distinct from
the real isolated installation acceptance used for the packaging handoff.

The Director still owns `pyproject.toml` and packaging adoption. The unmodified
repository wheel lacks the required Sigma data/license; it must fail acceptance.
The inert package-data proposal and isolated-build evidence remain in
[the packaging handoff](sigma-package-data-handoff.md). After adoption, rebuild
and repeat archive/installed acceptance and all exact-SHA CI/release gates.
