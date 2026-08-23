# Security policy

## Reporting

Report security issues privately to the repository owner. Do not open a public
issue containing credentials, customer designs, unpublished CAD, or vendor
account details.

## Credential handling

- Store runtime credentials in `~/.config/designstudio/env` or an approved
  secret manager, never in the checkout.
- Commit only `.env.example` files containing inert placeholders.
- Do not commit screenshots of vendor portals, access tokens, client secrets,
  private keys, signed download URLs, or local Codex permission files.
- Revoke and replace a credential immediately if it enters Git history, even
  if the repository is private or the commit is later removed.

The CI secret scan uses the repository's `.gitleaks.toml` configuration. Any
new allowlist entry must be narrowly scoped and document why the value is not a
credential.

## Private engineering data

Downloaded references, model weights, training corpora, campaign outputs, and
generated CAD stay outside Git. Keep only source, lock metadata, provenance,
small immutable test fixtures, and reproducible generation instructions here.
