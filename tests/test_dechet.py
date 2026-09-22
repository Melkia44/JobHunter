"""Correctifs « déchet » du 22/09/2026 : filtre domaine hors IT + archivage des offres anciennes."""
from datetime import date

import pytest

from job_hunter.collectors.base import is_off_domain
from job_hunter.sheet_writer import _posted_label, stale_rows


@pytest.mark.parametrize(
    "title",
    [
        "Chef de projets Environnement H/F",
        "Chef de Projet CVC F/H",
        "Chef de projet R&D Fromagerie (F/H)",
        "Chef de Projet Industrialisation F/H",
        "Chef de projet HSE H/F",
        "Chef(fe) de Projet TCE H/F",
        "Chef de projet marketing et communication H/F",
        "Assistant Chef de Chantier - Génie Civil H/F",
        "PROJECT MANAGER PIPE INSTALLATION H/F",
    ],
)
def test_off_domain_excluded(make_job, title):
    assert is_off_domain(make_job(title=title))


@pytest.mark.parametrize(
    "title",
    [
        "Service Delivery Manager H/F",
        "Chef de projet H/F",  # générique : c'est le scoring qui tranche
        "Chef de Projet Intégration ServiceNow (F/H)",
        "Chef de projet IT ERP agroalimentaire H/F",  # marqueur IT → sauvé
        "Chef de projet SIRH - compétences RH (H/F)",
        "Data Engineer Marketing – sur Nantes",
        "PO Technique / Chef de Projet Transformation IT – Environnements Cloud & Legacy (H/F)",
        "Product Owner Data Marketing - H/F - Nantes",
    ],
)
def test_it_titles_kept(make_job, title):
    assert not is_off_domain(make_job(title=title))


def test_posted_label():
    assert _posted_label(date(2026, 9, 21)) == "21/09/2026"
    assert _posted_label(None) == "n.c."


def _row(collected: str, status: str) -> list[str]:
    return [collected, "Acme", "SDM", "", "", "", "", "https://x", "", "", status]


def test_stale_rows_only_old_nouvelle():
    today = date(2026, 9, 22)
    rows = [
        _row("09/07/2026", "Nouvelle"),  # 0 : vieille et jamais traitée → archivée
        _row("20/09/2026", "Nouvelle"),  # 1 : récente → gardée
        _row("09/07/2026", "Candidature envoyée"),  # 2 : statut manuel → jamais touchée
        _row("pas une date", "Nouvelle"),  # 3 : date illisible → ignorée
        ["09/07/2026", "Acme"],  # 4 : ligne incomplète → ignorée
    ]
    assert stale_rows(rows, today, 30) == [0]
