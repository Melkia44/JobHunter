"""Parseurs d'alertes mail, testés sur de vrais mails anonymisés (tests/fixtures/alerts)."""
import base64
import email
from datetime import date
from email import policy
from pathlib import Path

import pytest

from job_hunter.collectors.email_alerts_collector import collect, parse_message
from job_hunter.config import Settings

FIX = Path(__file__).parent / "fixtures" / "alerts"


def _parse(name: str):
    msg = email.message_from_bytes((FIX / name).read_bytes(), policy=policy.default)
    return parse_message(msg)


def test_linkedin_multi_offres():
    jobs = _parse("linkedin_5offres.eml")
    assert len(jobs) == 5
    first = jobs[0]
    assert (first.title, first.company, first.location) == ("Product manager H/F", "CEGEDIM", "Nantes")
    assert first.url == "https://www.linkedin.com/jobs/view/4467508563/"  # URL canonique, sans tracking
    assert first.source == "email_linkedin"
    assert first.posted_at == date(2026, 9, 18)


def test_linkedin_mentions_ignorees():
    """« 2 anciens collègues », « 1 relation », « Postulez avec… » ne sont pas pris pour le lieu."""
    jobs = _parse("linkedin_6offres.eml")
    assert len(jobs) == 6
    amoe = next(j for j in jobs if j.company == "Inetum")
    assert (amoe.title, amoe.location) == ("CHEF PROJET AMOE", "La Chapelle-sur-Erdre")


def test_hellowork_dedup_et_champs():
    """Le mail répète les offres (liste + « publiées dernièrement ») → une seule fois chacune."""
    jobs = _parse("hellowork_sdm_multi.eml")
    titles = [(j.title, j.company) for j in jobs]
    assert len(titles) == len(set(titles)) == 9
    sdm = next(j for j in jobs if j.company == "Lynx RH")
    assert sdm.title == "Services Delivery Manager - Responsable Informatique H/F"
    assert (sdm.location, sdm.contract_type, sdm.salary_min, sdm.salary_max) == ("Nantes - 44", "CDI", 40, 47)
    assert all(j.source == "email_hellowork" for j in jobs)


def test_hellowork_hors_zone_et_pied_de_mail_ignores():
    jobs = _parse("hellowork_it_9offres.eml")
    assert not any(j.location.endswith(("- 21", "- 78")) for j in jobs)  # Dijon, Yvelines
    assert not any(j.title == "Espace candidat" for j in jobs)
    assert len(jobs) == 16


def test_indeed_jobalert():
    jobs = _parse("indeed_jobalert_5offres.eml")
    assert len(jobs) == 5
    pmo = jobs[0]
    assert (pmo.title, pmo.company, pmo.location) == (
        "PMO - Project Management officer IT H/F", "Scalian", "Saint-Herblain (44)",
    )
    assert (pmo.salary_min, pmo.salary_max) == (38, 43)
    assert pmo.url == "https://fr.indeed.com/viewjob?jk=7875ddb15f44de5f"  # même format que jobspy


def test_indeed_annonce_sponsorisee_ignoree():
    jobs = _parse("indeed_jobalert_pagead.eml")
    assert len(jobs) == 6  # 7 blocs dont 1 annonce /pagead/ sans jk
    assert not any("DataForce" in j.company for j in jobs)


def test_indeed_match_non_traite():
    assert _parse("indeed_match_ignore.eml") == []


def test_collect_sans_identifiants_skippe():
    assert collect(Settings(alerts_imap_user="", alerts_imap_password="")) == []


@pytest.mark.parametrize("name", sorted(p.name for p in FIX.glob("*.eml")))
def test_fixtures_sans_donnees_personnelles(name):
    raw = (FIX / name).read_text(encoding="utf-8", errors="replace").lower()
    for marker in ("lowagie", "melkia", "otptoken", "midtoken", "trackingid"):
        assert marker not in raw


def test_hellowork_lien_tracking_decode_en_url_canonique():
    from job_hunter.collectors.email_alerts_collector import _hw_canonical_url
    payload = "candidat@example.com\U0001FAA2https://www.hellowork.com/fr-fr/emplois/71724082.html?utm_source=jobalert&utm_medium=email"
    token = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    href = f"https://emails.hellowork.com/clic/8fea7697/6/39fb7030/{token}"
    assert _hw_canonical_url(href) == "https://www.hellowork.com/fr-fr/emplois/71724082.html"
    # Format inattendu → lien d'origine, jamais d'exception
    assert _hw_canonical_url("https://emails.hellowork.com/clic/x/0") == "https://emails.hellowork.com/clic/x/0"
