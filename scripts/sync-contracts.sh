#!/usr/bin/env sh
# Copies the contracts this service implements from qone-docs into contracts/openapi/, recording
# the ref and commit they came from. The copies are generated files: the source of truth stays
# in qone-docs (07-api), and the contract test validates responses against these copies so CI
# needs no access to the private documentation repository (ADR-009).
#   scripts/sync-contracts.sh [ref]      default ref: main
# Needs the GitHub CLI signed in with read access to code-corhuila/qone-docs.
set -eu
REF="${1:-main}"
REPO="code-corhuila/qone-docs"
DIR="contracts/openapi"
FILES="_shared.yaml qone-advisor-api.yaml"
mkdir -p "$DIR"
SHA=$(gh api "repos/$REPO/commits/$REF" --jq '.sha')
for f in $FILES; do
  gh api "repos/$REPO/contents/07-api/contracts/openapi/$f?ref=$REF" -H "Accept: application/vnd.github.raw" > "$DIR/$f"
  echo "synced $f"
done
printf 'repository: %s\nref: %s\ncommit: %s\nsynced: %s\nfiles: %s\n' "$REPO" "$REF" "$SHA" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$FILES" > "$DIR/SOURCE"
echo "contracts at $DIR from $REPO@$REF ($SHA)"
