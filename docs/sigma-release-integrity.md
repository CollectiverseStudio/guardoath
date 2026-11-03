# Sigma release asset consistency

The standalone wheel verifier and default shipped catalog/routing loaders now
compare all nine parsed batch JSON objects with fixed SHA-256 fingerprints in
`guardoath/sigma_rules/release_assets.py`. The first six anchors were frozen after the
128-rule immutable source audit at upstream revision
`05d1031f537e830b76da53bae2400c5814c89165`. All original IDs, authors, paths,
licenses, detections and authored positive/look-alike events are covered.

The concrete prior gap was reproduced without executing archive code: changing
rule `9fbf5927-5261-4284-a71d-f681029ea574` to a compatible canary detection while
retaining its original YAML/digests still passed the structural asset verifier.
That archive now fails. Changing both snapshot bytes and their self-declared
digests also fails; an artifact cannot redefine its expected release fingerprint.

Canonical serialization uses sorted object keys, compact separators, UTF-8,
unescaped Unicode and no nonfinite numbers. Parsed object key order, whitespace,
Unicode escaping and Windows line endings do not alter the fingerprint. Array
order and all field values remain significant. Strict duplicate-key/size/member
checks remain in front of archive acceptance. The default routing loader checks
the actual batch objects it reads, even after a prior catalog validation.

Run the existing archive command against the actual trusted newly built wheel:

```sh
python -m guardoath.sigma_rules.verify_distribution path/to/guardoath.whl
```

Batches 007 through 009 each add a separately reviewed anchor after independent Git/fixture verification.
The current contract is **203 pinned rules / 213 required assets**. Tests cover
changed detections in every one of the 203 rules, nine-batch metadata/fixture
tampering, self-rehashed snapshots, harmless reformatting, default-loader
rejection and archive inspection without importing archive modules. Generic
`build_evidence_catalog(batches)` / `RoutingTable(batches)` remain explicitly
caller-owned preparation APIs, rather than pretending every custom input is a
reviewed release. They retain their existing schema/matcher validation.

These anchors prove consistency with the verifier's reviewed release data.
They are not upstream signatures, a defense against replacing trusted verifier
code, detector effectiveness evidence, tenant authority or deployment approval.
Archive verification executes no archive code; isolated installed-artifact
acceptance remains a separate required check for trusted builds. No runtime YAML
dependency, network lookup or model call is introduced.

For a future source batch or intentional metadata/fixture change, first verify
the complete metadata/detection against immutable upstream Git objects using
`verify_sources`, review source/license and overlap evidence, then deliberately
update the corresponding code anchor with tests in the same reviewed batch.
Never accept an expected fingerprint supplied by the archive or generate anchors
automatically during verification. The Director's reserved packaging adoption
and release gates remain open and independent.

An isolated copy with the already proposed package-data entry was rebuilt with
the C10 Sigma code: its real wheel passed 128/128/135 inspection, then a local
`--no-index --no-deps --target` installation outside the checkout loaded release
anchors from that installation and passed all 128 routing previews and original
positive/look-alike pairs under `python -I`. This validates the proposal only;
the repository's reserved `pyproject.toml` is still unchanged and its unmodified
wheel is not claimed ready for distribution.
