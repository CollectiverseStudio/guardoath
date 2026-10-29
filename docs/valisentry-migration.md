# Valisentry brand and compatibility migration

## Decision
Product name: **Valisentry**. Former working name: Valinator. This is a brand transition, not an assertion of renamed Python modules or live new API behavior.

## Compatibility-first sequence
1. Publish product naming in README, docs and future UI text. Keep legacy identifiers intact for the moment.
2. Implement optional validation-code routing behind authenticated application policies per [validation-packs](validation-packs.md); no user-text policy override.
3. Add a `valisentry` Python import namespace and CLI entry point with backwards-compatible `valinator` shims; migrate tests and packaging without breaking imports.
4. Transition `VALINATOR_*` environment variables to `VALISENTRY_*` with documented precedence and conflict rejection; do not leak secrets.
5. Plan repository rename `CollectiverseStudio/valinator` → `CollectiverseStudio/valisentry` after verifying CI, deployment links, Git remotes, README clone instructions, integrations, badges, package indexes and automation. GitHub may redirect repository URLs, but do not rely on redirects for all clients or GitHub Actions references.
6. Keep old API routes stable until explicit versioned deprecation; test clients using existing endpoints and tokens.

## Release gates
- All current Python/API/dashboard tests pass, including Windows launcher and CI.
- No forced branch operations or destructive file moves; keep git history.
- Search and reconcile all package/import names, Docker/CI/deployment references, docs, URLs, config keys and stored identifiers before removing old paths.
- Add migration documentation and automated compatibility tests for both old and new names.
- Validate availability of repository name and trademark/domain separately before public rollout; no availability claim is made here.

## Current state
Only product-facing documentation has been rebranded. Repository, directory, Python module and API identifiers have not been renamed. Validation-code API routing is specified, not implemented.
