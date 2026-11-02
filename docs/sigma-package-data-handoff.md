# Sigma package-data handoff to the Director

**Proposed only.** The repository's reserved `pyproject.toml` is unchanged.
The current C14 source inventory is **153 pinned rules / 161 required assets**.
The historical C6 proof below remains unchanged; the same proposed glob entry was
rebuilt with batch007 and the repeatable installed-acceptance command passed 153
positive/benign/empty/malformed/routing cases each in a fresh offline venv. Actual
repository packaging adoption remains Director-owned and unimplemented.
[The inert patch](patches/sigma-package-data.patch) adds one package-data entry:

```toml
"guardoath.sigma_rules" = ["README.md", "LICENSE.Detection.Rules.md", "*.json", "upstream/batch_*/*.yml"]
```

This includes the batch metadata, attribution/evidence JSON, original snapshots,
README and retained DRL license. It adds no dependencies, provider connector,
model, endpoint or runtime permission. The existing privacy/static data settings
are preserved. No deployment or package publication was performed.

## Concrete proof

Tested with source implementation `8d374a05531efab941c1b6339861d4ce5d38dce0` in an
isolated temporary source copy, with only the proposed package-data line changed.
The actual repository file was compared before/after and remained identical.
`git apply --check docs/patches/sigma-package-data.patch` passed without applying it.

A real wheel built with the existing setuptools backend passed
`guardoath.sigma_rules.verify_distribution`:

- **128 rules, 128 pinned rules, 135 required assets** (six batch JSON files,
  128 original YAML snapshots, retained DRL license).
- Source revision: `05d1031f537e830b76da53bae2400c5814c89165`.
- Canonical license SHA-256:
  `d5f4c36a0528e01cfdd9e4fc60879201d3af49b41c520200643d321fe53da5f9`.

The proposed wheel was installed with `pip --no-index --no-deps --target` into a
separate temporary directory. Isolated `python -I` loaded the package from that
installation, not the checkout: the imported module's `__file__` was checked.
Catalog and routing each loaded all 128 rules; all **128 positive/look-alike pairs**
passed against their individual configured rules, with immutable source attribution.
No network, source fallback or live provider was involved. This is a real proposed
artifact test, not evidence that the current unmodified repository wheel is fixed.

## Director adoption and verification

The Director owns the actual packaging change. On the Director's normal reviewed
branch, inspect/apply the patch, build a fresh wheel with the configured backend,
then run the archive contract against that exact wheel:

```sh
git apply --check docs/patches/sigma-package-data.patch
# Apply only under the Director's acknowledged ownership/review process.
git apply docs/patches/sigma-package-data.patch
python -m guardoath.sigma_rules.verify_distribution PATH_TO_BUILT_WHEEL
```

Then repeat the trusted installed-artifact smoke test outside the checkout with
no PYTHONPATH fallback: assert the module originates from the isolated install,
load the 128 pinned evidence records and 128 routed rules, and run each shipped
positive/look-alike fixture using its original product/category and permitted ID.
Archive inspection alone does not execute or establish correctness of archive code;
the isolated installation test is a separate step for the trusted built artifact.

Before merge/release, verify focused/full tests, all 12 required CI jobs and CodeQL
for the exact packaging implementation SHA. Hosted event routing, tenant authority,
production request/workflow/checks and cost gates remain separate. The Director's
Python 3.14 secrets investigation also remains separately owned; no test budgets
or assertions were changed by this proposal.
