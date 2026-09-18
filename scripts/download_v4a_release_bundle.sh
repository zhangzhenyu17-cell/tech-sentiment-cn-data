#!/usr/bin/env bash
set -euo pipefail

TAG="${1:?release tag required}"
BASE="${2:?asset base required}"
DEST="${3:?destination required}"

mkdir -p "$DEST"
ARCHIVE="${BASE}.tar.gz"
MANIFEST="${BASE}.manifest.json"
SHA_FILE="${BASE}.sha256"

gh release download "$TAG" \
  --pattern "$ARCHIVE" \
  --pattern "$MANIFEST" \
  --pattern "$SHA_FILE" \
  --dir "$DEST"

for name in "$ARCHIVE" "$MANIFEST" "$SHA_FILE"; do
  test -f "${DEST}/${name}" || {
    echo "persistent bundle asset missing after download: ${name}" >&2
    exit 2
  }
done

expected="$(awk '{print $1}' "${DEST}/${SHA_FILE}")"
actual="$(sha256sum "${DEST}/${ARCHIVE}" | awk '{print $1}')"
[[ "$expected" == "$actual" ]] || {
  echo "persistent bundle sha256 sidecar mismatch: ${BASE}" >&2
  exit 3
}
echo "downloaded persistent bundle: ${BASE}"
