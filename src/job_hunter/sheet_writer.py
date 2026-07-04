"""Écriture Google Sheet : offres détectées, statuts employeurs, compteurs pipeline.

Déviation actée vs brief §12 : le référentiel employeurs reste data/employers.yaml
(les aliases du tier-matching n'existent pas dans le Sheet, et le scoring ne doit
pas dépendre du réseau). L'onglet 'Cibles employeurs' n'est lu que pour la mise à
jour des statuts.
"""
import re
from collections import Counter
from datetime import date

from google.oauth2 import service_account
from googleapiclient.discovery import build
from loguru import logger

from job_hunter.config import Settings
from job_hunter.models import Employer, ScoredJob
from job_hunter.normalizer import normalize
from job_hunter.scoring.tier import find_employer

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Noms RÉELS des onglets (relevés sur le classeur le 04/07/2026 — diffèrent du brief)
TAB_OFFERS = "Offres"
TAB_EMPLOYERS = "Cibles"
TAB_PILOTAGE = "Repères & pipeline"

SOURCE_LABELS = {
    "jobspy_indeed": "Indeed",
    "jobspy_glassdoor": "Glassdoor",
    "jobspy_google": "Google Jobs",
    "jobspy_linkedin": "LinkedIn",
    "france_travail": "France Travail",
    "apec_rss": "APEC",
}

PIPELINE_STATUSES = [
    "À cibler", "Offre repérée", "Candidature envoyée", "En cours",
    "Entretien", "Relance", "Stand-by", "Refus",
]

_TIER_SUFFIX_RE = re.compile(r"\s*\(p[1-3]\)$")


class SheetWriter:
    def __init__(self, settings: Settings) -> None:
        if not settings.spreadsheet_id:
            raise RuntimeError("SPREADSHEET_ID manquant (.env ou secret GHA)")
        if not settings.service_account_path.exists():
            raise RuntimeError(f"Clé Service Account absente : {settings.service_account_path}")
        creds = service_account.Credentials.from_service_account_file(
            str(settings.service_account_path), scopes=SCOPES
        )
        self._svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
        self._sheet_id = settings.spreadsheet_id

    def check_access(self) -> str:
        """Valide auth + partage (403 = non partagé avec le SA) + présence des 3 onglets."""
        meta = (
            self._svc.spreadsheets()
            .get(spreadsheetId=self._sheet_id, fields="properties.title,sheets.properties.title")
            .execute()
        )
        tabs = [sh["properties"]["title"] for sh in meta.get("sheets", [])]
        missing = [t for t in (TAB_OFFERS, TAB_EMPLOYERS, TAB_PILOTAGE) if t not in tabs]
        if missing:
            raise RuntimeError(
                f"Onglet(s) introuvable(s) : {', '.join(missing)} — présents : {', '.join(tabs)}"
            )
        return meta["properties"]["title"]

    # --- Onglet 'Offres détectées' -------------------------------------------

    def append_offers(self, retained: list[ScoredJob], today: date) -> int:
        """Append en bas de tableau. Dédup Sheet par URL (colonne Lien) — clé stable,
        contrairement au champ Employeur des lignes curées à la main (« Manitou Group
        (P1) — site officiel », etc.). Le couple (Employeur nettoyé, Intitulé) reste
        en filet secondaire."""
        seen_rows = self._read(f"'{TAB_OFFERS}'!B2:H")
        existing_urls = {r[6].strip() for r in seen_rows if len(r) >= 7 and r[6].strip()}
        existing_pairs = {
            (_clean_employer(r[0]), normalize(r[1])) for r in seen_rows if len(r) >= 2
        }
        rows: list[list] = []
        for sj in retained:
            pair = (_clean_employer(sj.job.company), normalize(sj.job.title))
            if sj.job.url in existing_urls or pair in existing_pairs:
                continue
            rows.append(_offer_row(sj, today))
            existing_urls.add(sj.job.url)
            existing_pairs.add(pair)
        if rows:
            self._svc.spreadsheets().values().append(
                spreadsheetId=self._sheet_id,
                range=f"'{TAB_OFFERS}'!A:L",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": rows},
            ).execute()
        logger.info(f"Sheet : {len(rows)} offre(s) ajoutée(s) dans '{TAB_OFFERS}'")
        return len(rows)

    # --- Onglet 'Cibles employeurs' -------------------------------------------

    def update_employer_statuses(
        self, touched: set[str], employers: list[Employer], today: date
    ) -> int:
        """`À cibler` → `Offre repérée` pour les employeurs cibles ayant une offre
        aujourd'hui. Ne dégrade jamais un statut avancé (Candidature envoyée, etc.)."""
        if not touched:
            return 0
        rows = self._read(f"'{TAB_EMPLOYERS}'!A1:L60")
        found = _find_header(rows)
        if found is None:
            logger.warning(f"'{TAB_EMPLOYERS}' : ligne d'en-têtes introuvable, statuts non mis à jour")
            return 0
        h_idx, header = found  # l'onglet a une ligne de titre AU-DESSUS des en-têtes

        def col(name: str) -> int | None:
            return header.index(normalize(name)) if normalize(name) in header else None

        c_emp, c_statut = col("Employeur"), col("Statut")
        c_der, c_proch = col("Dernière action"), col("Prochaine action")
        if None in (c_emp, c_statut, c_der, c_proch):
            logger.warning(f"'{TAB_EMPLOYERS}' : colonnes attendues introuvables, skip")
            return 0

        data, count = [], 0
        for idx, row in enumerate(rows[h_idx + 1 :], start=h_idx + 2):  # idx = n° de ligne Sheet
            name = row[c_emp] if len(row) > c_emp else ""
            emp = find_employer(name, employers) if name else None
            if emp is None or emp.name not in touched:
                continue
            statut = normalize(row[c_statut]) if len(row) > c_statut else ""
            if statut not in ("", "a cibler"):
                continue  # statut avancé : on n'y touche pas
            data += [
                {"range": f"'{TAB_EMPLOYERS}'!{_col(c_statut)}{idx}", "values": [["Offre repérée"]]},
                {"range": f"'{TAB_EMPLOYERS}'!{_col(c_der)}{idx}",
                 "values": [[f"Offre détectée le {today.strftime('%d/%m/%Y')}"]]},
                {"range": f"'{TAB_EMPLOYERS}'!{_col(c_proch)}{idx}",
                 "values": [["Analyser l'offre + postuler"]]},
            ]
            count += 1
        if data:
            self._batch_update(data)
        logger.info(f"Sheet : {count} statut(s) employeur passé(s) à 'Offre repérée'")
        return count

    # --- Onglet 'Repères marché & pilotage' -----------------------------------

    def recompute_pilotage(self) -> None:
        """Recalcule les compteurs depuis les statuts employeurs. Adaptatif : met à
        jour la cellule à droite de chaque libellé trouvé ; si l'onglet ne contient
        aucun libellé connu, écrit le bloc complet en A1."""
        emp_rows = self._read(f"'{TAB_EMPLOYERS}'!A1:L60")
        found = _find_header(emp_rows)
        if found is None:
            return
        h_idx, header = found
        c_emp = header.index("employeur")
        c_statut = header.index("statut")
        body_rows = [r for r in emp_rows[h_idx + 1 :] if len(r) > c_emp and r[c_emp].strip()]
        counts = Counter(
            normalize(r[c_statut]) if len(r) > c_statut else "" for r in body_rows
        )
        by_label = {s: counts.get(normalize(s), 0) for s in PIPELINE_STATUSES}
        total = len(body_rows)

        # Scan de toute la grille : les libellés ne sont pas en colonne A sur le vrai
        # classeur (relevés vers L11-L19). On écrit le compte dans la cellule à droite
        # de chaque libellé trouvé. Aucune écriture si rien trouvé (document curé :
        # pas de fallback destructif).
        pil = self._read(f"'{TAB_PILOTAGE}'!A1:Y60")
        data = []
        for r, row in enumerate(pil, start=1):
            for c, cell in enumerate(row):
                label = normalize(cell) if cell else ""
                if not label or c + 1 > 24:  # au-delà de Y : hors périmètre du scan
                    continue
                value: int | None = None
                for status in PIPELINE_STATUSES:
                    if label == normalize(status):
                        value = by_label[status]
                if label == "total cibles":
                    value = total
                if value is not None:
                    data.append(
                        {"range": f"'{TAB_PILOTAGE}'!{_col(c + 1)}{r}", "values": [[value]]}
                    )
        if not data:
            logger.warning(f"'{TAB_PILOTAGE}' : aucun libellé de statut trouvé, compteurs non écrits")
            return
        self._batch_update(data)
        logger.info(f"Sheet : compteurs pipeline recalculés ({total} cibles)")

    # --- Interne ----------------------------------------------------------------

    def _read(self, range_: str) -> list[list[str]]:
        resp = (
            self._svc.spreadsheets()
            .values()
            .get(spreadsheetId=self._sheet_id, range=range_)
            .execute()
        )
        return resp.get("values", [])

    def _batch_update(self, data: list[dict]) -> None:
        self._svc.spreadsheets().values().batchUpdate(
            spreadsheetId=self._sheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": data},
        ).execute()


def _find_header(rows: list[list[str]], max_scan: int = 5) -> tuple[int, list[str]] | None:
    """Ligne d'en-têtes = première ligne contenant 'Employeur' ET 'Statut'.
    Nécessaire : l'onglet réel a une ligne de titre fusionnée au-dessus des en-têtes."""
    for i, row in enumerate(rows[:max_scan]):
        norm = [normalize(c) for c in row]
        if "employeur" in norm and "statut" in norm:
            return i, norm
    return None


def _col(index: int) -> str:
    return chr(ord("A") + index)  # suffisant : ≤ 12 colonnes


def _clean_employer(cell: str) -> str:
    """Normalise un Employeur du Sheet, y compris les formats manuels :
    « Manitou Group (P1) — site officiel » → « manitou group »."""
    s = normalize(cell).split("—")[0].strip()
    return _TIER_SUFFIX_RE.sub("", s).strip()


def _offer_row(sj: ScoredJob, today: date) -> list:
    job = sj.job
    employer = (
        f"{job.company} (P{sj.matched_employer_tier})" if sj.matched_employer_tier else job.company
    )
    return [
        today.strftime("%d/%m/%Y"),
        employer,
        job.title,
        job.location or "n.c.",
        job.contract_type or "n.c.",
        _salary_label(job.salary_min, job.salary_max),
        _age_label(job.posted_at, today),
        job.url,
        _source_label(job.source, job.company),
        sj.match_reason,
        "Nouvelle",
        round(sj.score) / 100,  # col L « % Compatibilité » (format % → 0,70 s'affiche 70 %)
    ]


def _salary_label(mn: int | None, mx: int | None) -> str:
    if mn and mx:
        return f"{mn}–{mx} k€"
    if mn:
        return f"{mn} k€+"
    return "n.c."


def _age_label(posted: date | None, today: date) -> str:
    if posted is None:
        return "récent"
    days = max(0, (today - posted).days)
    return "1 jour" if days <= 1 else f"{days} jours"


def _source_label(source: str, company: str) -> str:
    if source == "careers_site":
        return f"Site officiel {company}"
    return SOURCE_LABELS.get(source, source)
