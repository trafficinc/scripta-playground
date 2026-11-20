#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/scan-secrets.sh [history|working-tree|staged]

  history       Scan Git history across all refs (default).
  working-tree  Scan current files, including untracked and ignored files.
  staged        Scan staged changes before committing.

Requires gitleaks on PATH: brew install gitleaks
Secrets are redacted. Exits nonzero if secrets are found or the scan fails.
EOF
}

if [[ $# -gt 1 ]]; then
  usage >&2
  exit 2
fi

case "${1:-history}" in
  history|working-tree|staged) mode="${1:-history}" ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

if ! command -v gitleaks >/dev/null 2>&1; then
  echo 'gitleaks is required. Install it with: brew install gitleaks' >&2
  exit 127
fi

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$repo_root"

case "$mode" in
  history)
    exec gitleaks git --no-banner --redact=100 --log-opts="--all --full-history" .
    ;;
  working-tree)
    exec gitleaks dir --no-banner --redact=100 .
    ;;
  staged)
    exec gitleaks git --no-banner --redact=100 --staged .
    ;;
esac
