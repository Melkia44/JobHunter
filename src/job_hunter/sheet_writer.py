"""Écriture Google Sheet : offres détectées, statuts employeurs, compteurs pipeline.

Déviation actée vs brief §12 : le référentiel employeurs reste data/employers.yaml
(les aliases du tier-matching n'existent pas dans le Sheet, et le scoring ne doit
pas dépendre du réseau). L'onglet 'Cibles employeurs' n'est lu que pour la mise à
jour des statuts.
"""
import re
from collections import Counter
from datetime import date, datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build
from loguru import logger

from job_hunter.collectors.base import is_excluded_contract, is_off_domain
from job_hunter.config import Settings
from job_hunter.models import Employer, RawJob, ScoredJob
from job_hunter.normalizer import canonical_company, canonical_title, normalize
from job_hunter.scoring.tier import find_employer

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Noms RÉELS des onglets (relevés sur le classeur le 04/07/2026 — diffèrent du brief)
TAB_OFFERS = "Offres"
TAB_EMPLOYERS = "Cibles"
TAB_PILOTAGE = "Repères & pipeline"
TAB_SOURCES = "Sources"
TAB_IMPLANTATIONS = "Implantations"  # créé au 1er run s'il n'existe pas

IMPLANTATION_HEADERS = [
    "Détecté le", "Entreprise", "Enseigne", "Commune", "Km Nantes", "Créé le",
    "Secteur", "NAF", "Effectif groupe", "Catégorie", "SIRET", "Fiche", "Statut", "Notes",
]

# Sources logiques (base.SOURCES) → libellé affiché dans l'onglet 'Sources'
SOURCE_ROW_LABELS = {
    "jobspy": "jobspy (Indeed / Google)",
    "france_travail": "France Travail",
    "apec": "APEC (RSS)",
    "careers_sites": "Sites carrières",
    "email_alerts": "Alertes mail (LinkedIn / Hellowork / Indeed)",
}

SOURCE_LABELS = {
    "jobspy_indeed": "Indeed",
    "jobspy_glassdoor": "Glassdoor",
    "jobspy_google": "Google Jobs",
    "jobspy_linkedin": "LinkedIn",
    "france_travail": "France Travail",
    "apec_rss": "APEC",
    "email_linkedin": "LinkedIn (alerte)",
    "email_hellowork": "Hellowork (alerte)",
    "email_indeed": "Indeed (alerte)",
}

PIPELINE_STATUSES = [
    "À cibler", "Offre repérée", "Candidature envoyée", "En cours",
    "Entretien", "Relance", "Stand-by", "Refus",
]

_TIER_SUFFIX_RE = re.compile(r"\s*\(p[1-3]\)$")

# Colonne M de l'onglet Offres : pourquoi l'outil a archivé la ligne (traçabilité)
REASON_HEADER = "Motif archivage"


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
            (canonical_company(_clean_employer(r[0])), canonical_title(r[1]))
            for r in seen_rows
            if len(r) >= 2
        }
        rows: list[list] = []
        for sj in retained:
            pair = (canonical_company(_clean_employer(sj.job.company)), canonical_title(sj.job.title))
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

    def archive_stale_offers(self, today: date, days: int) -> int:
        """Passe en « Archivée » les offres restées « Nouvelle » plus de `days` jours :
        la plupart ont expiré chez la source. Jamais bloquant."""
        try:
            rows = self._read(f"'{TAB_OFFERS}'!A2:K")
            idx = stale_rows(rows, today, days)
            if idx:
                self._archive([(i, f"Plus de {days} j sans suite") for i in idx])
            logger.info(f"Sheet : {len(idx)} offre(s) « Nouvelle » de plus de {days} j archivée(s)")
            return len(idx)
        except Exception as exc:  # noqa: BLE001 — nettoyage non critique
            logger.warning(f"Archivage des offres anciennes non effectué : {exc}")
            return 0

    def revalidate_offers(self) -> int:
        """Repasse sur les lignes « Nouvelle » avec les règles actuelles et archive,
        motif en colonne M : doublon, contrat hors CDI, métier hors IT, lieu hors zone.
        Ne supprime jamais, ne touche jamais une ligne au statut modifié à la main.
        Jamais bloquant."""
        try:
            rows = self._read(f"'{TAB_OFFERS}'!A2:K")
            found = revalidate_rows(rows)
            if found:
                self._archive(found)
            logger.info(f"Sheet : {len(found)} offre(s) « Nouvelle » archivée(s) à la revalidation")
            return len(found)
        except Exception as exc:  # noqa: BLE001 — nettoyage non critique
            logger.warning(f"Revalidation des offres non effectuée : {exc}")
            return 0

    def _archive(self, items: list[tuple[int, str]]) -> None:
        """items = (index 0-based relatif à A2, motif) → K « Archivée » + M motif."""
        head = self._read(f"'{TAB_OFFERS}'!M1:M1")
        data = [] if head and head[0] else [
            {"range": f"'{TAB_OFFERS}'!M1", "values": [[REASON_HEADER]]}
        ]
        for i, reason in items:
            data.append({"range": f"'{TAB_OFFERS}'!K{i + 2}", "values": [["Archivée"]]})
            data.append({"range": f"'{TAB_OFFERS}'!M{i + 2}", "values": [[reason]]})
        self._batch_update(data)

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

    # --- Onglet 'Sources' (tableau de bord) -----------------------------------

    def update_sources_status(self, stats: dict[str, tuple[int, bool]], run_date: str) -> None:
        """1 ligne/source : volume du dernier run, statut OK/NOK, date du dernier run
        réussi. Ne touche que les sources checkées (présentes dans stats) ; les autres
        gardent leur ligne, et 'Dernier run complet' n'avance que si la source est OK.
        Jamais bloquant : un souci ici (onglet absent…) ne casse pas l'écriture principale."""
        try:
            order = ["jobspy", "france_travail", "apec", "careers_sites", "email_alerts"]
            existing = {r[0]: r for r in self._read(f"'{TAB_SOURCES}'!A2:D10") if r}
            rows: list[list] = []
            for key in order:
                label = SOURCE_ROW_LABELS[key]
                prev = existing.get(label, [label, "", "", "—"])
                if key in stats:
                    count, ok = stats[key]
                    prev_date = prev[3] if len(prev) > 3 else "—"
                    rows.append([label, count, "OK" if ok else "NOK", run_date if ok else prev_date])
                else:  # source non sélectionnée ce run → on préserve sa ligne
                    rows.append([(prev[i] if len(prev) > i else "") for i in range(4)])
            self._batch_update([{"range": f"'{TAB_SOURCES}'!A2:D{1 + len(order)}", "values": rows}])
            logger.info(f"Sheet : tableau '{TAB_SOURCES}' mis à jour")
        except Exception as exc:  # noqa: BLE001 — tableau de bord non critique, jamais bloquant
            logger.warning(f"'{TAB_SOURCES}' non mis à jour : {exc}")

    def sort_offers(self) -> None:
        """Trie 'Offres' : date de repérage (A) décroissante, puis % compatibilité (L).
        Sans ce tri, l'append pose les nouveautés tout en bas (ligne 200+), sous
        l'historique — incident du 25/09/2026. Non bloquant."""
        try:
            meta = self._svc.spreadsheets().get(
                spreadsheetId=self._sheet_id, fields="sheets.properties(sheetId,title)"
            ).execute()
            sheet_id = next(
                sh["properties"]["sheetId"] for sh in meta["sheets"]
                if sh["properties"]["title"] == TAB_OFFERS
            )
            self._svc.spreadsheets().batchUpdate(
                spreadsheetId=self._sheet_id,
                body={"requests": [{"sortRange": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 1},  # garde l'en-tête
                    "sortSpecs": [
                        {"dimensionIndex": 0, "sortOrder": "DESCENDING"},
                        {"dimensionIndex": 11, "sortOrder": "DESCENDING"},
                    ],
                }}]},
            ).execute()
            logger.info(f"Sheet : '{TAB_OFFERS}' trié (plus récentes en haut)")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"'{TAB_OFFERS}' non trié : {exc}")

    # --- Onglet 'Implantations' ----------------------------------------------

    def append_implantations(self, items: list, today: date) -> int:
        """Append des nouvelles implantations. Dédup par SIRET (colonne K) : le Sheet
        est la seule mémoire — pas de DB à committer pour cette veille hebdo."""
        self._ensure_tab(TAB_IMPLANTATIONS, IMPLANTATION_HEADERS)
        existing = {r[0].strip() for r in self._read(f"'{TAB_IMPLANTATIONS}'!K2:K") if r}
        rows = [
            [
                today.strftime("%d/%m/%Y"), i.entreprise, i.enseigne, i.commune,
                "" if i.km_nantes is None else i.km_nantes, i.date_creation, i.secteur,
                i.naf, i.effectif_groupe, i.categorie, f"'{i.siret}",
                i.url, "À qualifier", "",
            ]
            for i in items
            if i.siret not in existing
        ]
        if rows:
            self._svc.spreadsheets().values().append(
                spreadsheetId=self._sheet_id,
                range=f"'{TAB_IMPLANTATIONS}'!A:N",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": rows},
            ).execute()
        logger.info(f"Sheet : {len(rows)} implantation(s) ajoutée(s) dans '{TAB_IMPLANTATIONS}'")
        return len(rows)

    def _ensure_tab(self, title: str, headers: list[str]) -> None:
        meta = self._svc.spreadsheets().get(
            spreadsheetId=self._sheet_id, fields="sheets.properties.title"
        ).execute()
        if title in {sh["properties"]["title"] for sh in meta.get("sheets", [])}:
            return
        self._svc.spreadsheets().batchUpdate(
            spreadsheetId=self._sheet_id,
            body={"requests": [{"addSheet": {"properties": {
                "title": title, "gridProperties": {"frozenRowCount": 1}}}}]},
        ).execute()
        self._batch_update([{"range": f"'{title}'!A1", "values": [headers]}])
        logger.info(f"Sheet : onglet '{title}' créé")

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
        _posted_label(job.posted_at),
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


def _posted_label(posted: date | None) -> str:
    """Date de publication réelle (col G). Remplace l'ancien « N jours », figé au jour
    de la collecte et donc trompeur dès le lendemain (relevé du 22/09/2026)."""
    return posted.strftime("%d/%m/%Y") if posted else "n.c."


def stale_rows(rows: list[list[str]], today: date, days: int) -> list[int]:
    """Index (0-based, relatifs à rows) des offres encore « Nouvelle » collectées il y a
    plus de `days` jours. rows = A2:K de l'onglet Offres (A = date de collecte, K = statut).
    Les lignes au statut modifié à la main ne sont jamais touchées."""
    out: list[int] = []
    for i, row in enumerate(rows):
        if len(row) < 11 or normalize(row[10]) != "nouvelle":
            continue
        try:
            collected = datetime.strptime(row[0].strip(), "%d/%m/%Y").date()
        except ValueError:
            continue  # date illisible : on ne touche pas
        if (today - collected).days > days:
            out.append(i)
    return out


def _source_label(source: str, company: str) -> str:
    if source == "careers_site":
        return f"Site officiel {company}"
    return SOURCE_LABELS.get(source, source)


def revalidate_rows(rows: list[list[str]]) -> list[tuple[int, str]]:
    """(index 0-based relatif à rows, motif) des lignes « Nouvelle » à archiver.
    rows = A2:K de l'onglet Offres (B employeur, C intitulé, D lieu, E contrat,
    H lien, K statut). Règles sûres uniquement — le score complet n'est pas rejoué :
    la Sheet ne garde pas la description qui a fait retenir certaines offres.
    Doublon = même lien ou même (employeur, intitulé) canoniques qu'une ligne PLUS
    HAUTE non archivée (la première occurrence active est gardée ; une ligne archivée
    ne sert jamais de référence, sinon les deux exemplaires finiraient archivés)."""
    out: list[tuple[int, str]] = []
    seen_urls: dict[str, int] = {}
    seen_pairs: dict[tuple[str, str], int] = {}
    for i, row in enumerate(rows):
        cell = lambda c: row[c].strip() if len(row) > c else ""  # noqa: E731
        url, title = cell(7), cell(2)
        pair = (canonical_company(_clean_employer(cell(1))), canonical_title(title))
        first = seen_urls.get(url) if url else None
        if first is None and title:
            first = seen_pairs.get(pair)
        status = normalize(cell(10))
        if status != "nouvelle" or not title:
            if status and status != "archivee":  # suivie à la main : référence
                if url:
                    seen_urls.setdefault(url, i)
                seen_pairs.setdefault(pair, i)
            continue
        if first is not None:
            out.append((i, f"Doublon de la ligne {first + 2}"))
            continue
        if url:
            seen_urls.setdefault(url, i)
        seen_pairs.setdefault(pair, i)
        contract = cell(4)
        job = RawJob.model_construct(
            title=title, contract_type=None if contract in ("", "n.c.") else contract
        )
        if is_excluded_contract(job):
            out.append((i, "Contrat hors CDI"))
        elif is_off_domain(job):
            out.append((i, "Métier hors IT"))
        elif "(hors zone)" in cell(3).lower():
            out.append((i, "Lieu hors zone"))
    return out
