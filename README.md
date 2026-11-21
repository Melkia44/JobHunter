# job-hunter

Veille emploi automatique : chaque nuit, collecte des offres (Indeed, Google Jobs,
France Travail, APEC, sites carrières, alertes mail LinkedIn / Hellowork / Indeed),
supprime les doublons, note chaque offre sur 100 selon **ton** profil et écrit les
meilleures dans un **Google Sheet**. Tourne gratuitement sur GitHub Actions.

Tout ce qui te concerne (métiers visés, compétences, zone, sources) tient dans un seul
fichier : [`data/profile.yaml`](data/profile.yaml). Le reste ne se touche pas.

> ⚠️ Indeed, Google Jobs et l'APEC sont lus par scraping, ce que leurs CGU n'autorisent
> pas forcément. Usage personnel, à faible volume, sous ta responsabilité.

---

## Démarrer (≈ 30 min, tout depuis le navigateur)

### 1. Ton propre dépôt

Sur la page du dépôt : **Use this template → Create a new repository**, visibilité
**Private** (ton profil de recherche n'a pas à être public).

### 2. Ton profil

Dans ton dépôt, ouvre `data/profile.yaml` → ✏️ (modifier) et remplace le profil
d'exemple (SDM / chef de projet IT autour de Nantes) par le tien. Chaque bloc est
commenté. L'essentiel :

| Bloc | À renseigner |
|---|---|
| `sources` | Les sources que tu as configurées (commence par `jobspy`, `france_travail`, `apec`) |
| `titles` | Intitulés visés, avec un poids (100 = cœur de cible) |
| `hard_skills` / `soft_skills` | Mots-clés qui rapportent des points dans les descriptions |
| `location` | Ville centre (code commune **INSEE**, lat/lon), rayon, villes proches, départements |
| `search` | Requêtes Indeed/Google, mots-clés APEC, codes métier **ROME** France Travail |
| `filters.it_only` | `true` écarte les métiers hors IT. **`false` pour un profil non IT** |

Les codes ROME se trouvent sur [francetravail.fr](https://www.francetravail.fr) (fiches
métiers) et le code INSEE d'une commune sur [insee.fr](https://www.insee.fr/fr/recherche/recherche-geographique)
(≠ code postal : Paris = 75056, Boulogne-Billancourt = 92012).

Astuce : colle le fichier dans Claude avec ton CV ou ta fiche de poste visée et
demande-lui de l'adapter.

### 3. Google Sheet + compte de service

1. Crée un Google Sheet **vide** ; son ID est dans l'URL : `docs.google.com/spreadsheets/d/<ID>/edit`.
2. [console.cloud.google.com](https://console.cloud.google.com) → nouveau projet →
   *API et services* → activer **Google Sheets API**.
3. *IAM → Comptes de service* → créer (aucun rôle) → *Clés* → **Ajouter une clé JSON**
   (un fichier se télécharge).
4. **Partage le Sheet** avec l'email du compte de service
   (`xxx@yyy.iam.gserviceaccount.com`) en **Éditeur**.
   Sans ce partage : erreur **403**, de loin la plus fréquente.

Les onglets (Offres, Cibles, Repères & pipeline, Sources) sont créés au premier run.

### 4. France Travail

[francetravail.io](https://francetravail.io) → créer une application → API
**« Offres d'emploi v2 »** → noter `client_id` / `client_secret`.

### 5. Secrets GitHub

Dépôt → *Settings → Secrets and variables → Actions → New repository secret* :

| Secret | Contenu |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Contenu **complet** du fichier JSON de l'étape 3 |
| `SPREADSHEET_ID` | ID du Sheet |
| `FRANCE_TRAVAIL_CLIENT_ID` | client_id |
| `FRANCE_TRAVAIL_CLIENT_SECRET` | client_secret |
| `ALERTS_IMAP_USER` / `ALERTS_IMAP_PASSWORD` | *Optionnel*, cf. « Alertes mail » |
| `INSEE_API_KEY` | *Optionnel*, cf. « Veille implantations » |

Puis *Settings → Actions → General → Workflow permissions* = **Read and write**
(le run commite sa base anti-doublons `data/seen_jobs.db`).

### 6. Premier run

*Actions → Daily Job Hunt → Run workflow* :
1. coche **dry_run** → collecte + scoring sans rien écrire ; le résumé du run liste
   le top 3 et les éventuelles erreurs de config ;
2. relance **sans** dry_run → les offres arrivent dans le Sheet.

Ensuite, ça tourne seul chaque nuit (≈ 00h Paris, souvent retardé de quelques heures
par GitHub).

---

## Régler la sélection

- **Trop peu d'offres** : baisser `MIN_SCORE` (défaut 65), élargir `titles` /
  `jobspy_queries`, ajouter des `hard_skills`.
- **Trop de bruit** : retirer les requêtes ambiguës, passer les intitulés flous en
  `gated: true` (profil IT), monter `MIN_SCORE`.

Seuils : variables `MIN_SCORE` / `MIN_SCORE_TIER1` (dans `.env` en local ; en CI, les
défauts 65 / 58 s'appliquent, ajouter `MIN_SCORE: "60"` dans `env:` de `daily.yml`
pour changer).

Score /100 = intitulé 30 % · compétences 25 % · localisation 20 % · employeur cible
15 % · soft skills 10 % (sans description : intitulé 55 % · localisation 30 % ·
employeur 15 %).

## Comportement

collecte → **CDI uniquement** → filtre hors IT (si `it_only`) → dédup (employeur +
intitulé) → score → seuil → Sheet.

À chaque run, l'onglet **Offres** est aussi revalidé (lignes « Nouvelle » devenues
hors critères → « Archivée », motif en colonne M), purgé (« Nouvelle » > 30 j →
« Archivée ») et trié (récentes en haut). Change toi-même la colonne **Statut**
(Postulée, Entretien…) : l'outil ne touche qu'aux lignes « Nouvelle ».

**Run en échec (mail GitHub)** si une source plante ou remonte 0 offre — sauf l'APEC,
souvent bloquée (403) depuis les serveurs GitHub : signalée dans l'onglet Sources sans
faire échouer le run. Une source listée dans `profile.yaml › sources` mais non
configurée fait donc échouer chaque run : ne liste que ce que tu as configuré.

LinkedIn n'est **jamais** scrapé depuis GitHub (ban rapide) : passer par les alertes
mail, ou en local `./run_locally.sh --include-linkedin`.

---

## Options

### Alertes mail (LinkedIn, Hellowork, Indeed)

1. Boîte Gmail **dédiée**, IMAP activé, **mot de passe d'application** Google
   (compte Google → Sécurité → Validation en 2 étapes → Mots de passe des applications).
2. Crée tes alertes sur LinkedIn / Hellowork / Indeed avec cette adresse (ou transfère-les).
3. Secrets `ALERTS_IMAP_USER` / `ALERTS_IMAP_PASSWORD`, et ajoute `email_alerts` dans
   `profile.yaml › sources`.

Lecture seule (aucun mail marqué lu), 3 derniers jours à chaque run. Les alertes
d'autres plateformes sont ignorées (un parseur par gabarit de mail :
`collectors/email_alerts_collector.py`).

### Employeurs cibles et sites carrières

`data/employers.yaml` (vide par défaut) : bonus de score pour les employeurs listés
(P1/P2/P3) et, avec `careers_url` + `ats` (SmartRecruiters, Teamtailor, Greenhouse,
Lever, Cegid…), lecture directe de leur site carrières → ajouter `careers_sites` dans
les sources. Format en commentaire dans le fichier ; exemple complet :
`tests/fixtures/employers.yaml`.

### Veille implantations (hebdo)

Nouveaux établissements d'entreprises ≥ 50 salariés dans un département, secteurs en
liste blanche → onglet **Implantations** (cible : candidatures spontanées).
Régler `departement` et `centre_lambert93` dans `data/implantations.yaml`, clé
[portail-api.insee.fr](https://portail-api.insee.fr) → **API Sirene** → secret
`INSEE_API_KEY`. Sans clé, le workflow du lundi ne fait rien.

---

## En local (optionnel)

Prérequis : [uv](https://docs.astral.sh/uv/) (installe Python 3.13 tout seul).

```bash
uv sync
cp .env.example .env                 # remplir ; clé JSON → secrets/service_account.json
uv run job-hunter setup              # vérifie config + accès Sheet, crée les onglets
uv run job-hunter run --dry-run      # tout le pipeline, sans écriture
uv run job-hunter run --sources france_travail --dry-run   # une source (debug)
uv run job-hunter -v run --dry-run   # + logs détaillés et offres brutes
uv run job-hunter run --min-score 55
uv run pytest                        # tests (profil figé : tests/fixtures/profile.yaml)
```

## Licence

[MIT](LICENSE).
