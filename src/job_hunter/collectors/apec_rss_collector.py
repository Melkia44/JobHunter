"""Collecteur APEC — flux RSS des recherches sauvegardées (data/apec_feeds.yaml).

Pas d'API publique APEC : chaque recherche sauvegardée expose un flux RSS sans auth.
Trade-off accepté : description tronquée par le flux, pas de fetch de la page
complète (scraping HTML fragile). company/location sont extraits au mieux du flux —
mapping à affiner sur les vrais flux au STOP (format RSS APEC non documenté).
"""
from datetime import date
from pathlib import Path
from typing import Any

import feedparser
import yaml
from loguru import logger

from job_hunter.config import Settings
from job_hunter.models import RawJob


def collect(settings: Settings) -> list[RawJob]:
    feeds = _load_feeds(settings.apec_feeds_yaml)
    if not feeds:
        logger.warning("apec_feeds.yaml vide ou absent, source APEC skippée")
        return []

    jobs: dict[str, RawJob] = {}  # clé = link → dédup intra-source
    for feed in feeds:
        parsed = feedparser.parse(feed["url"])
        if getattr(parsed, "bozo", False) and not parsed.entries:
            logger.warning(f"apec[{feed['name']}] : flux illisible — {parsed.get('bozo_exception')}")
            continue
        new = 0
        for entry in parsed.entries:
            job = _to_raw_job(entry)
            if job is not None and job.url not in jobs:
                jobs[job.url] = job
                new += 1
        logger.debug(f"apec[{feed['name']}] : {len(parsed.entries)} entrées, {new} nouvelles")

    logger.info(f"apec : {len(jobs)} offres uniques ({len(feeds)} flux)")
    return list(jobs.values())


def _load_feeds(path: Path) -> list[dict[str, str]]:
    """Liste [{name, url}] depuis le YAML. Fichier absent/vide/malformé → [] (jamais planter)."""
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        logger.warning(f"apec_feeds.yaml malformé : {exc}")
        return []
    feeds = data.get("feeds") or []
    valid = [f for f in feeds if isinstance(f, dict) and f.get("name") and f.get("url")]
    if len(valid) < len(feeds):
        logger.warning(f"apec_feeds.yaml : {len(feeds) - len(valid)} entrée(s) sans name/url ignorée(s)")
    return valid


def _to_raw_job(entry: Any) -> RawJob | None:
    url = (entry.get("link") or "").strip()
    title = (entry.get("title") or "").strip()
    if not url or not title:
        return None
    return RawJob(
        source="apec_rss",
        external_id=entry.get("id") or url,
        title=title,
        # 'author' porte parfois la société côté APEC ; sinon fallback assumé.
        # ⚠ fingerprint dédup = company|title : le fallback peut sur-dédupliquer
        # deux offres homonymes de sociétés différentes — à réévaluer sur flux réels.
        company=(entry.get("author") or "").strip() or "Inconnue (APEC)",
        location="",  # non structuré dans le RSS — à extraire du titre/summary si format le permet
        contract_type=None,
        salary_min=None,
        salary_max=None,
        remote_pct=None,
        description=(entry.get("summary") or "").strip() or None,
        url=url,
        posted_at=_published_date(entry),
        raw={k: str(v) for k, v in dict(entry).items()},
    )


def _published_date(entry: Any) -> date | None:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if t is None:
        return None
    try:
        return date(t.tm_year, t.tm_mon, t.tm_mday)
    except (AttributeError, ValueError):
        return None
