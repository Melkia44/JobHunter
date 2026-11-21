# job-hunter

Veille emploi quotidienne : collecte multi-sources (JobSpy, France Travail, API APEC,
sites carrières, alertes mail LinkedIn / Hellowork / Indeed), déduplication persistante,
scoring déterministe /100, écriture dans un Google Sheet + rapport CLI. Tourne via
GitHub Actions et en local.

## Prérequis

- Python 3.13 (`.python-version`, CI) — minimum supporté : 3.11
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

### 4. APEC

Rien à configurer : le RSS APEC n'existe plus (07/2026). Le collecteur interroge l'API
JSON publique du site (`rechercheOffre`) avec des mots-clés définis dans
`collectors/apec_rss_collector.py` (`KEYWORDS`), puis filtre sur 44/49/85/35.
⚠️ APEC est derrière DataDome : depuis l'IP de GitHub Actions les requêtes peuvent
être rejetées en **403** (source à 0 → run en échec, cf. « Comportement »).

### 5. Alertes mail (LinkedIn, Hellowork, Indeed)

1. Boîte Gmail dédiée, IMAP activé, **mot de passe d'application** Google.
2. Créer les alertes sur chaque plateforme avec cette adresse (ou transférer).
3. `ALERTS_IMAP_USER` / `ALERTS_IMAP_PASSWORD` dans `.env` et en secrets GitHub.

Lecture seule (aucun mail marqué lu), fenêtre glissante `ALERTS_LOOKBACK_DAYS` (3 j).
Seuls ces expéditeurs exacts sont parsés — tout autre mail est ignoré :

| Plateforme | Expéditeur |
|---|---|
| LinkedIn | `jobalerts-noreply@linkedin.com` |
| Hellowork | `alerte@emails.hellowork.com` |
| Indeed | `donotreply@jobalert.indeed.com` |

Ajouter une plateforme = un expéditeur dans `SENDERS` + un parseur + une fixture `.eml`.

## Utilisation

```bash
uv run job-hunter setup                          # vérifie config + prérequis
uv run job-hunter run                            # run complet (sans LinkedIn)
uv run job-hunter run --dry-run                  # pas d'écriture Sheet
uv run job-hunter run --sources france_travail   # une source seule (debug)
uv run job-hunter run --min-score 55             # override du seuil
uv run job-hunter -v run --dry-run               # logs DEBUG + dump des RawJob
./run_locally.sh --include-linkedin              # LinkedIn — LOCAL UNIQUEMENT
```

Sources : `jobspy`, `france_travail`, `apec`, `careers_sites`, `email_alerts`.

LinkedIn (scraping JobSpy) n'est **jamais** activé sur GitHub Actions (IP cloud = ban
rapide). Les alertes mail LinkedIn, elles, tournent en CI.

## Comportement du pipeline

collecte → filtre **CDI uniquement** → filtre **hors IT** → dédup (empreinte
employeur + intitulé) → scoring → seuil (`MIN_SCORE` 65, `MIN_SCORE_TIER1` 58 pour
les employeurs cibles) → Sheet.

À chaque run, l'onglet **Offres** est aussi : revalidé (lignes « Nouvelle » qui ne
passent plus les filtres → archivées, motif en col. M), purgé (« Nouvelle » > 30 j →
« Archivée »), trié (plus récentes en haut). Onglets mis à jour : **Cibles**,
**Repères & pipeline**, **Sources** (état OK/NOK par source).

Les offres nouvelles ne sont marquées vues qu'après écriture Sheet réussie.

**Run volontairement en échec** si une source lève une erreur ou remonte 0 offre :
GitHub notifie par mail, les offres des autres sources sont déjà écrites et la DB
dédup est quand même commitée.

## Planification (GitHub Actions)

| Workflow | Cron (UTC) | Rôle |
|---|---|---|
| `daily.yml` | `47 22 * * *` (≈ 00h47 Paris été) | `run` complet, sans LinkedIn |
| `implantations.yml` | `47 4 * * 1` (lundi) | veille Sirene |
| `sync-statuts.yml` | `17 * * * *` | synchro Café Emploi |

GitHub retarde souvent les crons de plusieurs heures. Lancement manuel du daily :
option `dry_run` = **alertes mail uniquement**, aucune écriture.

## CI — Secrets GitHub Actions

Repo → Settings → Secrets and variables → Actions :

| Secret | Contenu |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | JSON complet de la clé du SA |
| `FRANCE_TRAVAIL_CLIENT_ID` | Client ID francetravail.io |
| `FRANCE_TRAVAIL_CLIENT_SECRET` | Client Secret |
| `SPREADSHEET_ID` | ID du Sheet cible |
| `ALERTS_IMAP_USER` | Boîte dédiée aux alertes mail (LinkedIn, Hellowork, Indeed) |
| `ALERTS_IMAP_PASSWORD` | Mot de passe d'application Google de cette boîte |
| `INSEE_API_KEY` | Clé API Sirene (workflow implantations) |

Vérifier aussi Settings → Actions → General → Workflow permissions =
**Read and write** (le workflow commit `data/seen_jobs.db` après chaque run).

## Tests

```bash
uv run pytest -v
```

Périmètre testé : normalisation, dédup, scoring (titre, hard skills, lieu, tier,
agrégation), filtres déchet, revalidation, parseurs d'alertes mail (fixtures `.eml`
anonymisées dans `tests/fixtures/alerts/`), lieu des sites carrières, implantations,
synchro statuts, alertes CLI. Les collecteurs réseau (JobSpy, France Travail, APEC,
sites carrières) se testent via `run --sources X --dry-run` sur données réelles.

## Veille implantations (hebdo)

Nouveaux établissements secondaires dans le 44 d'entreprises ≥ 50 salariés (ou ETI/GE),
secteurs en liste blanche (`data/implantations.yaml`) → onglet **Implantations** du Sheet
(créé au 1er run, dédup par SIRET). Cible : candidatures spontanées.

1. Clé : [portail-api.insee.fr](https://portail-api.insee.fr) → créer une application →
   souscrire **API Sirene** → `INSEE_API_KEY` dans `.env` et en secret GitHub.
2. Calibrage sur l'historique : `uv run job-hunter implantations --since 2026-03-01 --dry-run`
3. Run : `uv run job-hunter implantations` — planifié le lundi (`implantations.yml`).

## Suivi des candidatures depuis Café Emploi (synchro horaire)

Changer un statut dans Café Emploi dépose un JSON dans le dossier Drive
`CafeEmploi-inbox` ; `job-hunter sync-statuts` (workflow horaire `sync-statuts.yml`)
l'applique à l'onglet Offres : Statut (K), Date candidature (N, si vide, sur « Postulée »),
Relance le (O = +7 j). Filigrane dans l'onglet `Sync` (créé au 1er run).
Test : `uv run job-hunter sync-statuts --dry-run`.

Mise en place (une fois) :
1. GCP du compte de service : activer l'**API Google Drive**.
2. Partager le dossier Drive `CafeEmploi-inbox` avec l'email du SA en **Lecteur**.
