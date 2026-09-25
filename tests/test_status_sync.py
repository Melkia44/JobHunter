"""Synchro Café Emploi → onglet Offres."""
from datetime import datetime, timezone

from job_hunter.status_sync import Event, parse_event, plan_updates


def _row(url, status="Nouvelle", cand="", relance=""):
    row = [""] * 15
    row[7], row[10], row[13], row[14] = url, status, cand, relance
    return row


def _ev(url, status, hour=8, day=25):
    at = datetime(2026, 9, day, hour, 0, tzinfo=timezone.utc)
    return Event(url=url, status=status, at=at, created=at.isoformat())


def _cells(updates):
    return {u["range"]: u["values"][0][0] for u in updates}


def test_parse_rejects_unknown_status():
    assert parse_event({"url": "u", "status": "Bof"}, "2026-09-25T08:00:00Z") is None
    ev = parse_event({"url": "u", "status": "Postulée", "at": "2026-09-25T08:00:00Z"}, "x")
    assert ev.status == "Postulée"


def test_postulee_sets_date_and_relance():
    c = _cells(plan_updates([_row("a")], [_ev("a", "Postulée")], 7))
    assert c == {"'Offres'!K2": "Postulée", "'Offres'!N2": "25/09/2026", "'Offres'!O2": "02/10/2026"}


def test_postulee_keeps_existing_date():
    c = _cells(plan_updates([_row("a", cand="20/09/2026")], [_ev("a", "Postulée")], 7))
    assert "'Offres'!N2" not in c and c["'Offres'!O2"] == "27/09/2026"


def test_relancee_pushes_relance():
    c = _cells(plan_updates([_row("a", "Postulée", "01/09/2026", "08/09/2026")], [_ev("a", "Relancée")], 7))
    assert c["'Offres'!O2"] == "02/10/2026"


def test_entretien_clears_relance():
    c = _cells(plan_updates([_row("a", "Postulée", "01/09/2026", "08/09/2026")], [_ev("a", "Entretien")], 7))
    assert c == {"'Offres'!K2": "Entretien", "'Offres'!O2": ""}


def test_last_event_wins_and_paris_date():
    rows = [_row("x"), _row("a")]
    evs = [_ev("a", "Refus", hour=9), _ev("a", "Postulée", hour=23, day=24)]  # 23h UTC = 25/09 Paris
    c = _cells(plan_updates(rows, evs, 7))
    assert c == {"'Offres'!K3": "Refus"}


def test_unknown_url_ignored():
    assert plan_updates([_row("a")], [_ev("zzz", "Postulée")], 7) == []
