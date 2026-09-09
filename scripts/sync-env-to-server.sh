#!/usr/bin/env bash

set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-root@msk3.zanity.net}"
REMOTE_ENV_PATH="${REMOTE_ENV_PATH:-/root/remnawave-bot/.env}"
LOCAL_ENV_PATH="${LOCAL_ENV_PATH:-.env}"
CHECK_ONLY="${CHECK_ONLY:-0}"
APPLY_CHANGES="${APPLY_CHANGES:-0}"

usage() {
  cat <<'EOF'
Usage: sync-env-to-server.sh [--check-only] [--apply]

  --check-only   Compare local and remote .env, print diff, never upload
  --apply        Upload local .env to server when files differ

Default behavior:
  Shows diff and exits without uploading.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check-only)
      CHECK_ONLY="1"
      shift
      ;;
    --apply)
      APPLY_CHANGES="1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ ! -f "$LOCAL_ENV_PATH" ]]; then
  echo "Local file not found: $LOCAL_ENV_PATH" >&2
  exit 1
fi

if ! command -v ssh >/dev/null 2>&1; then
  echo "ssh command is required" >&2
  exit 1
fi

if ! command -v scp >/dev/null 2>&1; then
  echo "scp command is required" >&2
  exit 1
fi

if ! command -v diff >/dev/null 2>&1; then
  echo "diff command is required" >&2
  exit 1
fi

local_hash="$(sha256sum "$LOCAL_ENV_PATH" | awk '{print $1}')"

remote_hash="$(ssh "$REMOTE_HOST" "if [ -f '$REMOTE_ENV_PATH' ]; then sha256sum '$REMOTE_ENV_PATH' | awk '{print \$1}'; else echo MISSING; fi")"

echo "Local  hash: $local_hash"
echo "Remote hash: $remote_hash"

if [[ "$remote_hash" == "$local_hash" ]]; then
  echo "Config is already synchronized. Nothing to upload."
  exit 0
fi

tmp_remote_file="$(mktemp)"
cleanup() {
  rm -f "$tmp_remote_file"
}
trap cleanup EXIT

if [[ "$remote_hash" == "MISSING" ]]; then
  : > "$tmp_remote_file"
else
  ssh "$REMOTE_HOST" "cat '$REMOTE_ENV_PATH'" > "$tmp_remote_file"
fi

echo
echo "Diff between local and remote:"
if ! diff -u --label "remote:$REMOTE_ENV_PATH" --label "local:$LOCAL_ENV_PATH" "$tmp_remote_file" "$LOCAL_ENV_PATH"; then
  true
fi
echo

if [[ "$CHECK_ONLY" == "1" ]]; then
  echo "Config differs. Check-only mode enabled, upload skipped."
  exit 2
fi

if [[ "$APPLY_CHANGES" != "1" ]]; then
  echo "Config differs. Upload skipped by default."
  echo "Run with --apply to overwrite remote file: $REMOTE_HOST:$REMOTE_ENV_PATH"
  exit 2
fi

echo "Config differs. Uploading $LOCAL_ENV_PATH to $REMOTE_HOST:$REMOTE_ENV_PATH ..."
scp "$LOCAL_ENV_PATH" "$REMOTE_HOST:$REMOTE_ENV_PATH"

new_remote_hash="$(ssh "$REMOTE_HOST" "sha256sum '$REMOTE_ENV_PATH' | awk '{print \$1}'")"

if [[ "$new_remote_hash" != "$local_hash" ]]; then
  echo "Upload completed, but hash mismatch remains!" >&2
  echo "Expected: $local_hash" >&2
  echo "Actual:   $new_remote_hash" >&2
  exit 1
fi

echo "Sync complete. Remote config now matches local .env."
