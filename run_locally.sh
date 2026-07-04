#!/usr/bin/env bash
# Run local (Mac) — seul chemin autorisé pour --include-linkedin (IP résidentielle).
# Usage : ./run_locally.sh [--include-linkedin] [--dry-run] [--sources X] ...
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
    echo "ERREUR : .env manquant. Copier .env.example et remplir les valeurs." >&2
    exit 1
fi
if [[ ! -f secrets/service_account.json ]]; then
    echo "ERREUR : secrets/service_account.json manquant (clé JSON du Service Account GCP)." >&2
    exit 1
fi

exec uv run job-hunter run "$@"
