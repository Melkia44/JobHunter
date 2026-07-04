# job-hunter

Veille emploi quotidienne : collecte multi-sources (JobSpy, France Travail, RSS APEC,
sites carrières), déduplication persistante, scoring déterministe /100, écriture dans
un Google Sheet + rapport CLI. Tourne via GitHub Actions (cron quotidien) et en local.

## Prérequis

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) :
  - macOS/Linux : `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - Windows : `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

## Installation

```bash
uv sync
uv run job-hunter --help
```

## Configuration

### 1. Fichier `.env`

```bash
cp .env.example .env   # puis remplir les valeurs
```

### 2. Google Sheets — Service Account

1. Créer un projet GCP dédié, activer l'**API Google Sheets**.
2. Créer un Service Account (aucun rôle IAM projet nécessaire), générer une **clé JSON**.
3. Placer la clé dans `./secrets/service_account.json` (dossier gitignored).
4. **Partager le Google Sheet cible** avec l'email du SA
   (`xxx@yyy.iam.gserviceaccount.com`) en droits **Éditeur**.
   ⚠️ Sans ce partage, l'API renvoie **403** — c'est l'erreur la plus fréquente.

### 3. France Travail

Créer une application sur [francetravail.io](https://francetravail.io) avec l'API
« Offres d'emploi v2 » (scope `api_offresdemploiv2 o2dsoffre`), récupérer
`client_id` / `client_secret` → `.env`.

### 4. APEC (optionnel)

Créer des recherches sauvegardées sur apec.fr, récupérer leurs URLs de flux RSS
et les coller dans `data/apec_feeds.yaml`. Fichier vide = source APEC skippée.

## Utilisation

```bash
uv run job-hunter setup                          # vérifie config + prérequis
uv run job-hunter run                            # run complet (sans LinkedIn)
uv run job-hunter run --dry-run                  # pas d'écriture Sheet
uv run job-hunter run --sources france_travail   # une source seule (debug)
./run_locally.sh --include-linkedin              # LinkedIn — LOCAL UNIQUEMENT
```

LinkedIn n'est **jamais** activé sur GitHub Actions (IP cloud = ban rapide).

## CI — Secrets GitHub Actions

Repo → Settings → Secrets and variables → Actions :

| Secret | Contenu |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | JSON complet de la clé du SA |
| `FRANCE_TRAVAIL_CLIENT_ID` | Client ID francetravail.io |
| `FRANCE_TRAVAIL_CLIENT_SECRET` | Client Secret |
| `SPREADSHEET_ID` | ID du Sheet cible |

Vérifier aussi Settings → Actions → General → Workflow permissions =
**Read and write** (le workflow commit `data/seen_jobs.db` après chaque run).

## Tests

```bash
uv run pytest -v
```

Périmètre testé : normalisation, déduplication, scoring. Les collecteurs se testent
manuellement via `run --sources X --dry-run` sur données réelles.
