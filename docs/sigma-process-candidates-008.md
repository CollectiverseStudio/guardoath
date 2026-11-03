# Staged Windows process candidates: batch 008 (C16)

**C17 update:** This frozen candidate cohort is independently verified and adopted
into the current 178-rule default source catalog. Original staging statements
below describe the reviewed C16 handoff; see [default adoption](sigma-batch-008-integration.md).


This cohort stages **25 further compatible originals** from SigmaHQ revision
`05d1031f537e830b76da53bae2400c5814c89165`, excluding all 128 initial defaults and
25 C13 candidates. Complete original YAML, metadata, authors, IDs, references,
detections and immutable Git/SHA-256 hashes are retained in
`guardoath/sigma_rules/candidates/batch_008.json` and `candidates/batch_008/`.
The [Detection Rule License 1.1](../guardoath/sigma_rules/LICENSE.Detection.Rules.md)
applies. All 25 snapshots were read from the exact fetched Git commit and their
blob identities checked against its tree.

**This is staging evidence, not default adoption.** C14 owns the separate prior
153-rule integration; this cohort would extend the reviewed pool to 178, not
silently add executable controls. No default manifest, semantic release anchor,
package-data setting, routing logic or event budget is changed here.

## Original candidates

Each row retains a hand-authored matching event and look-alike in the JSON.
Status, severity, false-positive notes and source descriptions are preserved;
compatibility does not establish malicious intent or effectiveness.

| Original title | Rule ID | Upstream status / level |
| --- | --- | --- |
| Uncommon Child Process Of Defaultpack.EXE | `b2309017-4235-44fe-b5af-b15363011957` | test / medium |
| Remote File Download Via Desktopimgdownldr Utility | `214641c2-c579-4ecb-8427-0cf19df6842e` | test / medium |
| DeviceCredentialDeployment Execution | `b8b1b304-a60f-4999-9a6e-c547bde03ffd` | test / medium |
| Arbitrary MSI Download Via Devinit.EXE | `90d50722-0483-4065-8e35-57efaadd354d` | test / medium |
| Potentially Suspicious Child Process Of ClickOnce Application | `67bc0e75-c0a9-4cfc-8754-84a505b63c04` | test / medium |
| DirLister Execution | `b4dc61f5-6cce-468e-a608-b48b469feaa2` | test / low |
| Potentially Suspicious Child Process Of DiskShadow.EXE | `9f546b25-5f12-4c8d-8532-5893dcb1e4b8` | test / medium |
| DNS Exfiltration and Tunneling Tools Execution | `98a96a5a-64a0-4c42-92c5-489da3866cb0` | test / high |
| New DNS ServerLevelPluginDll Installed Via Dnscmd.EXE | `f63b56ee-3f79-4b8a-97fb-5c48007e8573` | test / high |
| Potential Application Whitelisting Bypass via Dnx.EXE | `81ebd28b-9607-4478-bf06-974ed9d53ed7` | test / medium |
| Esentutl Gather Credentials | `7df1713a-1a5b-4a4b-a071-dc83b144a101` | test / medium |
| File Explorer Folder Opened Using Explorer Folder Shortcut Via Shell | `c3d76afc-93df-461e-8e67-9b2bad3f2ac4` | test / high |
| Finger.EXE Execution | `af491bca-e752-4b44-9c86-df5680533dbc` | test / high |
| Use of FSharp Interpreters | `b96b2031-7c17-4473-afe7-a30ce714db29` | test / medium |
| Gpresult Display Group Policy Information | `e56d3073-83ff-4021-90fe-c658e0709e72` | test / medium |
| HTML Help HH.EXE Suspicious Child Process | `52cad028-0ff0-4854-8f67-d25dfcbc78b4` | test / high |
| HackTool - ADCSPwn Execution | `cd8c163e-a19b-402e-bdd5-419ff5859f12` | test / high |
| HackTool - F-Secure C3 Load by Rundll32 | `b18c9d4c-fac9-4708-bd06-dd5bfacf200f` | test / critical |
| HackTool - CreateMiniDump Execution | `36d88494-1d43-4dc0-b3fa-35c8fea0ca9d` | test / high |
| HackTool - Defendnot Execution | `0aaa9689-9f38-491d-bd4d-a9af6eb43138` | experimental / high |
| HackTool - DInjector PowerShell Cradle Execution | `d78b5d61-187d-44b6-bf02-93486a80de5a` | test / critical |
| HackTool - Doppelanger LSASS Dumper Execution | `d474c8fe-bb69-4ea0-b7d9-f682b56d52d3` | experimental / high |
| HackTool - Dumpert Process Dumper Execution | `2704ab9e-afe2-4854-a3b1-0c0706d03578` | test / critical |
| HackTool - EDRSilencer Execution | `eb2d07d4-49cb-4523-801a-da002df36602` | test / high |
| HackTool - Empire PowerShell Launch Parameters | `79f4ede3-402e-41c8-bc3e-ebbf5f162581` | test / high |

## Evidence and limits

The suite checks every original snapshot/hash and YAML-to-JSON field, original
attribution/logsource, positive/look-alike pairs, case semantics, required fields,
malformed values/containers and unsupported unselected branches. Sixteen additional
positive/look-alike pairs cover OriginalFileName, Description, IMPHASH alternatives,
child images, executable aliases and a literal two-space encoded-flag branch.
Known indicator hashes are public source patterns, not secret credentials.
All commands, URLs and process paths are inert event text; none is executed.

Explicit expectations cover all 178 reviewed definitions. For the finite new
fixtures, positives match only their target and look-alikes match none; none of
the 153 predecessors' current positive/look-alike fixtures match these new rules.
These exact finite results are not a guarantee against real-world overlaps, nor
an accuracy assessment. Source notes about legitimate activity still apply.

```bash
python -m pytest tests/test_sigma_process_candidates_008.py -q
```

The test review table can combine C13 staging with defaults before or after its
separately reviewed adoption, without duplicating an ID or replacing a source.
Every proposed process event selects 53 rules and excludes 125 script rules;
every script event selects 125 and excludes 53 process rules. One inherited budget
and one evaluation are used per event. The existing cap remains **128 selected
rules**: directly evaluating all 178 rejects. Default loaders cannot select these
unpublished IDs. No chunking, truncation, budget reset or tenant authority is added.

## Coordinated promotion plan

1. Independently verify all original IDs/metadata/detections/digests against the
   pinned Git commit and review examples, look-alikes and upstream limitations.
2. Coordinate the shipped batch manifest/snapshots, evidence counts and reviewed
   semantic release fingerprint. Freeze the expected anchor from reviewed source,
   not the artifact under inspection. Preserve all prior reviewed anchors.
3. Update source-route/inventory/whole-cohort overlap tests explicitly: a proposed
   178-definition release has 53 process and 125 script rules, while per-event 128
   limits and aggregate worker/deadline semantics stay unchanged.
   One new batch JSON plus 25 snapshots would add 26 required archive assets
   to the reviewed 153-rule/161-asset predecessor, for a proposed total of 187.
4. Coordinate default adoption assertions in this staging suite only after that
   reviewed change; keep the candidate snapshots and authored evidence as history.
5. The Director owns actual package-data adoption. Rebuild and verify the real
   archive and a fresh non-editable installed environment, updating exact reviewed
   file/count expectations rather than filling gaps from the checkout.
6. Require full matrix/browser tests and CodeQL at the exact integration SHA.
   Hosted registration, tenant entitlement, sign-in and deployment stay separate.

No packaging, default release readiness, hosted control or production effect is
claimed by this candidate batch.
