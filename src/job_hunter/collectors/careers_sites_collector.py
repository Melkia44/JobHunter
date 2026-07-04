"""Collecteur sites carrières — v1 : scraper Manitou uniquement.

Reco du 04/07/2026 sur les 5 employeurs prioritaires : CA-GIP et Arkéa bloqués
edge (anti-bot, robots.txt inclus — on ne contourne pas), Groupama et VYV/Harmonie
= SPA JS (API à capturer en DevTools, v2). Ces employeurs restent couverts par
France Travail / JobSpy, où le matching tier du scoring les repérera.

Déviation assumée vs brief : pas de filtre 48 h ici — une offre encore en ligne
chez un employeur cible reste pertinente, et la dédup rend l'ingestion idempotente.
"""
import re
import time
from datetime import date
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx
import yaml
from loguru import logger
from selectolax.parser import HTMLParser, Node

from job_hunter.config import Settings
from job_hunter.models import Employer, RawJob

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
}
TIMEOUT_S = 15.0
RATE_LIMIT_S = 2.0  # 1 req / 2 s par domaine
EXCLUDED_CONTRACTS = {"Alternance", "Stage"}  # hors cible candidat

_FR_MONTHS = {
    "janvier": 1, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "août": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "décembre": 12,
}


def collect(settings: Settings) -> list[RawJob]:
    employers = load_employers(settings.employers_yaml)
    if not employers:
        logger.warning("employers.yaml vide ou absent, source careers_sites skippée")
        return []

    jobs: dict[str, RawJob] = {}
    with httpx.Client(timeout=TIMEOUT_S, headers=HEADERS, follow_redirects=True) as client:
        for emp in employers:
            if not emp.careers_url:
                continue
            domain = urlparse(emp.careers_url).netloc
            scraper = SCRAPERS.get(domain)
            if scraper is None:
                logger.warning(f"careers_site : pas de scraper pour {domain}, skippé")
                continue
            try:
                found = scraper(client, emp)
            except Exception as exc:  # noqa: BLE001 — un site cassé ne bloque pas le run
                logger.warning(f"careers_site[{emp.name}] : échec — {exc}")
                continue
            for job in found:
                jobs.setdefault(job.url, job)
            time.sleep(RATE_LIMIT_S)

    logger.info(f"careers_sites : {len(jobs)} offres")
    return list(jobs.values())


def load_employers(path: Path) -> list[Employer]:
    """Référentiel employeurs (réutilisé par le scoring tier en Phase 3)."""
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        logger.warning(f"employers.yaml malformé : {exc}")
        return []
    return [Employer(**e) for e in (data.get("employers") or [])]


# --- Manitou (WordPress rendu serveur) --------------------------------------


def _scrape_manitou(client: httpx.Client, emp: Employer) -> list[RawJob]:
    """Listing /fr/offres/ : liens d'offres + métadonnées (date, contrat, ville)
    extraites du texte du bloc englobant — volontairement sans classes CSS,
    plus robustes aux refontes de thème que des sélecteurs précis."""
    resp = client.get(emp.careers_url)
    resp.raise_for_status()
    tree = HTMLParser(resp.text)

    jobs: list[RawJob] = []
    seen: set[str] = set()
    for a in tree.css("a[href*='/fr/offres/']"):
        href = (a.attributes.get("href") or "").split("#")[0]
        # Écarte la page de listing elle-même et les liens de navigation
        if href.rstrip("/").endswith("/fr/offres") or href in seen:
            continue
        title = a.text(strip=True)
        if not title:
            continue
        seen.add(href)

        meta = _closest_block_text(a)
        contract = next(
            (c for c in ("CDI", "CDD", "Alternance", "Stage") if re.search(rf"\b{c}\b", meta)),
            None,
        )
        if contract in EXCLUDED_CONTRACTS:
            continue
        city = _city_after_france(meta)
        jobs.append(
            RawJob(
                source="careers_site",
                external_id=href,
                title=title,
                company=emp.name,
                location=f"{city}, France" if city else "France",
                contract_type=contract,
                salary_min=None,
                salary_max=None,
                remote_pct=None,
                description=None,  # listing seul ; pas de fetch par offre (38 requêtes évitées)
                url=href,
                posted_at=_parse_fr_date(meta),
                raw={"employer": emp.name, "meta_text": meta[:500]},
            )
        )
    return jobs


def _closest_block_text(node: Node, max_up: int = 4) -> str:
    """Texte du <li>/<article> englobant (ou à défaut du parent à max_up niveaux)."""
    current = node
    for _ in range(max_up):
        if current.parent is None:
            break
        current = current.parent
        if current.tag in ("li", "article"):
            break
    return current.text(separator="\n", strip=True)


def _parse_fr_date(text: str) -> date | None:
    m = re.search(r"(\d{1,2})\s+([a-zûé]+)\s+(\d{4})", text.lower())
    if not m or m.group(2) not in _FR_MONTHS:
        return None
    try:
        return date(int(m.group(3)), _FR_MONTHS[m.group(2)], int(m.group(1)))
    except ValueError:
        return None


def _city_after_france(text: str) -> str | None:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    for i, line in enumerate(lines):
        if line == "France" and i + 1 < len(lines):
            return lines[i + 1]
    return None


SCRAPERS: dict[str, Callable[[httpx.Client, Employer], list[RawJob]]] = {
    "careers.manitou-group.com": _scrape_manitou,
}
