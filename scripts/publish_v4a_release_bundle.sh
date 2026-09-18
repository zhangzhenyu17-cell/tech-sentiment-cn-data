#!/usr/bin/env bash
set -euo pipefail

TAG="${1:?release tag required}"
DIR="${2:?bundle directory required}"
BASE="${3:?asset base required}"

ARCHIVE="${BASE}.tar.gz"
MANIFEST="${BASE}.manifest.json"
SHA_FILE="${BASE}.sha256"
ASSETS=("$ARCHIVE" "$MANIFEST" "$SHA_FILE")

for name in "${ASSETS[@]}"; do
  test -f "${DIR}/${name}" || {
    echo "missing bundle asset: ${DIR}/${name}" >&2
    exit 2
  }
done

if ! gh release view "$TAG" >/dev/null 2>&1; then
  if ! gh release create "$TAG" \
    --target "$GITHUB_SHA" \
    --title "V4-A reusable public stage bundles" \
    --notes "Immutable public-data stage bundles for manual V4-A qualification. No private model, signal, holding, research outcome, or trading authority is included."; then
    # A parallel stage may have created the fixed registry tag after our view.
    gh release view "$TAG" >/dev/null 2>&1 || {
      echo "unable to create or resolve persistent bundle release: ${TAG}" >&2
      exit 3
    }
  fi
fi

mapfile -t existing < <(gh release view "$TAG" --json assets --jq '.assets[].name')
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

missing_assets=()
for name in "${ASSETS[@]}"; do
  found=false
  for remote_name in "${existing[@]:-}"; do
    if [[ "$remote_name" == "$name" ]]; then
      found=true
      break
    fi
  done

  if $found; then
    gh release download "$TAG" --pattern "$name" --dir "$tmp"
    if ! cmp -s "${DIR}/${name}" "${tmp}/${name}"; then
      echo "persistent bundle asset exists with different bytes: ${name}" >&2
      exit 4
    fi
  else
    missing_assets+=("${DIR}/${name}")
  fi
done

if (( ${#missing_assets[@]} )); then
  # Upload only missing assets. Existing assets are never overwritten.
  gh release upload "$TAG" "${missing_assets[@]}"
fi

verify_dir="${tmp}/verify"
mkdir -p "$verify_dir"
gh release download "$TAG" \
  --pattern "$ARCHIVE" \
  --pattern "$MANIFEST" \
  --pattern "$SHA_FILE" \
  --dir "$verify_dir"

for name in "${ASSETS[@]}"; do
  test -f "${verify_dir}/${name}" || {
    echo "persistent bundle asset missing after publication: ${name}" >&2
    exit 5
  }
  if ! cmp -s "${DIR}/${name}" "${verify_dir}/${name}"; then
    echo "persistent bundle asset verification mismatch: ${name}" >&2
    exit 6
  fi
done

if (( ${#missing_assets[@]} )); then
  echo "published missing persistent bundle assets: ${BASE}"
else
  echo "persistent bundle already exists with identical bytes: ${BASE}"
fi
