"""Synchro des statuts saisis dans Café Emploi → onglet Offres.

Café Emploi (page claude.ai) ne peut pas écrire dans le Sheet : le connecteur Google
Drive sait seulement créer des fichiers. Chaque changement de statut y dépose donc
un petit JSON dans le dossier Drive « CafeEmploi-inbox » :
    {"v":1, "url": "...", "status": "Postulée", "at": "2026-09-25T08:40:00Z", ...}
Ce module (run horaire) lit les événements plus récents que le filigrane, les
applique (dernier gagnant par URL) puis avance le filigrane. Les fichiers ne sont
pas supprimés : le compte de service n'a qu'un accès lecture au dossier.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from loguru import logger

PARIS = ZoneInfo("Europe/Paris")

STATUSES = [
    "Nouvelle", "À postuler", "Postulée", "Relancée", "Entretien",
    "Offre", "Refus", "Pas intéressé", "Archivée",
]
_CLEAR_RELANCE = {"Nouvelle", "À postuler", "Entretien", "Offre", "Refus", "Pas intéressé", "Archivée"}

# Colonnes de l'onglet Offres (0-based)
COL_URL, COL_STATUS, COL_DATE_CAND, COL_RELANCE = 7, 10, 13, 14
RELANCE_HEADER = "Relance le"


@dataclass(frozen=True)
class Event:
    url: str
    status: str
    at: datetime  # UTC, aware
    created: str  # createdTime Drive (ISO) — sert de filigrane


def parse_event(raw: dict, created: str) -> Event | None:
    url = str(raw.get("url") or "").strip()
    status = str(raw.get("status") or "").strip()
    if not url or status not in STATUSES:
        return None
    try:
        at = datetime.fromisoformat(str(raw.get("at") or created).replace("Z", "+00:00"))
    except ValueError:
        return None
    return Event(url=url, status=status, at=at, created=created)


def _fr(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _parse_fr(s: str) -> date | None:
    try:
        return datetime.strptime(s.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def plan_updates(rows: list[list[str]], events: list[Event], relance_days: int) -> list[dict]:
    """rows = A2:O de l'onglet Offres. Renvoie les écritures {range, values} à faire.
    Événements triés par date : le dernier par URL gagne."""
    by_url: dict[str, int] = {}
    for i, row in enumerate(rows):
        if len(row) > COL_URL and row[COL_URL].strip():
            by_url.setdefault(row[COL_URL].strip(), i)
    latest: dict[str, Event] = {}
    for ev in sorted(events, key=lambda e: e.at):
        latest[ev.url] = ev
    out: list[dict] = []
    for url, ev in latest.items():
        i = by_url.get(url)
        if i is None:
            logger.warning(f"statut ignoré, offre introuvable dans le Sheet : {url}")
            continue
        r = i + 2
        row = rows[i]
        cell = lambda c: row[c].strip() if len(row) > c else ""  # noqa: E731
        day = ev.at.astimezone(PARIS).date()
        out.append({"range": f"'Offres'!K{r}", "values": [[ev.status]]})
        if ev.status == "Postulée":
            cand = _parse_fr(cell(COL_DATE_CAND)) or day
            if not cell(COL_DATE_CAND):
                out.append({"range": f"'Offres'!N{r}", "values": [[_fr(cand)]]})
            out.append({"range": f"'Offres'!O{r}", "values": [[_fr(cand + timedelta(days=relance_days))]]})
        elif ev.status == "Relancée":
            out.append({"range": f"'Offres'!O{r}", "values": [[_fr(day + timedelta(days=relance_days))]]})
        elif ev.status in _CLEAR_RELANCE and cell(COL_RELANCE):
            out.append({"range": f"'Offres'!O{r}", "values": [[""]]})
    return out


def fetch_events(creds, folder_id: str, after: str) -> tuple[list[Event], str]:
    """(événements, nouveau filigrane) — fichiers JSON du dossier créés après `after`
    (ISO, "" = tous). Le filigrane avance aussi au-delà des fichiers invalides."""
    import json

    from googleapiclient.discovery import build

    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    q = f"'{folder_id}' in parents and trashed = false and mimeType = 'application/json'"
    if after:
        q += f" and createdTime > '{after}'"
    files, token = [], None
    while True:
        resp = drive.files().list(
            q=q, fields="nextPageToken, files(id, name, createdTime)", orderBy="createdTime",
            pageSize=200, pageToken=token, supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        files += resp.get("files", [])
        token = resp.get("nextPageToken")
        if not token:
            break
    events = []
    for f in files:
        try:
            raw = json.loads(drive.files().get_media(fileId=f["id"]).execute())
        except Exception as exc:  # noqa: BLE001 — un fichier illisible ne bloque pas les autres
            logger.warning(f"{f['name']} illisible : {exc}")
            continue
        ev = parse_event(raw, f["createdTime"])
        if ev:
            events.append(ev)
    logger.info(f"Café Emploi : {len(files)} fichier(s) nouveau(x), {len(events)} statut(s) valide(s)")
    return events, max([after, *[f["createdTime"] for f in files]])
