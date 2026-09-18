#!/usr/bin/env bash
set -euo pipefail

TAG="${1:?release tag required}"
DIR="${2:?bundle directory required}"
BASE="${3:?asset base required}"

ARCHIVE="${BASE}.tar.gz"
MANIFEST="${BASE}.manifest.json"
SHA_FILE="${BASE}.sha256"

for name in "$ARCHIVE" "$MANIFEST" "$SHA_FILE"; do
  test -f "${DIR}/${name}" || {
    echo "missing bundle asset: ${DIR}/${name}" >&2
    exit 2
  }
done

if ! gh release view "$TAG" >/dev/null 2>&1; then
  gh release create "$TAG" \
    --target "$GITHUB_SHA" \
    --title "V4-A reusable public stage bundles" \
    --notes "Immutable public-data stage bundles for manual V4-A qualification. No private model, signal, holding, research outcome, or trading authority is included."
fi

mapfile -t existing < <(gh release view "$TAG" --json assets --jq '.assets[].name')
has_archive=false
has_manifest=false
has_sha=false
for name in "${existing[@]:-}"; do
  [[ "$name" == "$ARCHIVE" ]] && has_archive=true
  [[ "$name" == "$MANIFEST" ]] && has_manifest=true
  [[ "$name" == "$SHA_FILE" ]] && has_sha=true
done

if $has_archive || $has_manifest || $has_sha; then
  if ! $has_archive || ! $has_manifest || ! $has_sha; then
    echo "partial persistent bundle already exists for ${BASE}; refusing overwrite" >&2
    exit 3
  fi
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  gh release download "$TAG" \
    --pattern "$ARCHIVE" \
    --pattern "$MANIFEST" \
    --pattern "$SHA_FILE" \
    --dir "$tmp"
  cmp "${DIR}/${ARCHIVE}" "${tmp}/${ARCHIVE}"
  cmp "${DIR}/${MANIFEST}" "${tmp}/${MANIFEST}"
  cmp "${DIR}/${SHA_FILE}" "${tmp}/${SHA_FILE}"
  echo "persistent bundle already exists with identical bytes: ${BASE}"
  exit 0
fi

gh release upload "$TAG" \
  "${DIR}/${ARCHIVE}" \
  "${DIR}/${MANIFEST}" \
  "${DIR}/${SHA_FILE}"
echo "published persistent bundle: ${BASE}"
