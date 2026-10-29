# GuardOath clean rename — 2026-10-29

Product name: **GuardOath**. Former names: **Valinator** (working name) and **Valisentry** (discarded brand proposal).

Release **0.25.0** renames the Python package, imports, pip/npm names, environment variables, report directory, dashboard, tests, CI, and documentation. The repository URL is to be renamed to `CollectiverseStudio/guardoath` after the code PR is merged. No compatibility shims or fallback variables exist.

## Environment migration

Every old `VALINATOR_*` or `VALISENTRY_*` environment variable must be replaced with `GUARDOATH_*`. The startup guard refuses to run when any legacy prefix is set, naming the variable and replacement without revealing its value.

| Former name | New name |
|---|---|
| `VALINATOR_API_KEY` | `GUARDOATH_API_KEY` |
| `VALINATOR_POLICY_FILE` | `GUARDOATH_POLICY_FILE` |
| `VALINATOR_PACK_FILE` | `GUARDOATH_PACK_FILE` |
| `VALINATOR_ENABLE_PII` | `GUARDOATH_ENABLE_PII` |
| `VALINATOR_ENABLE_AZURE_PROMPT_SHIELDS` | `GUARDOATH_ENABLE_AZURE_PROMPT_SHIELDS` |
| `VALINATOR_CONTENT_SAFETY_PROVIDER` | `GUARDOATH_CONTENT_SAFETY_PROVIDER` |
| `VALINATOR_AZURE_CONTENT_SAFETY_ENDPOINT` | `GUARDOATH_AZURE_CONTENT_SAFETY_ENDPOINT` |
| `VALINATOR_AZURE_CONTENT_SAFETY_KEY` | `GUARDOATH_AZURE_CONTENT_SAFETY_KEY` |
| `VALINATOR_AWS_REGION` | `GUARDOATH_AWS_REGION` |
| `VALINATOR_BEDROCK_GUARDRAIL_ID` | `GUARDOATH_BEDROCK_GUARDRAIL_ID` |
| `VALINATOR_BEDROCK_GUARDRAIL_VERSION` | `GUARDOATH_BEDROCK_GUARDRAIL_VERSION` |
| `VALINATOR_CHECKS_PER_MINUTE` | `GUARDOATH_CHECKS_PER_MINUTE` |
| `VALINATOR_BURST` | `GUARDOATH_BURST` |
| `VALINATOR_APPLICATION_CONCURRENCY` | `GUARDOATH_APPLICATION_CONCURRENCY` |
| `VALINATOR_PROCESS_CONCURRENCY` | `GUARDOATH_PROCESS_CONCURRENCY` |
| `VALINATOR_INGRESS_CONCURRENCY` | `GUARDOATH_INGRESS_CONCURRENCY` |

## Outstanding

- Confirm ownership of guardoath.com before publishing links.
- Complete trademark search.
- Deploy and validate hosted demonstration.
- After merge, rename GitHub repository and update developer working trees and remotes.
