"""Collecteur JobSpy : Indeed, Glassdoor, Google Jobs (+ LinkedIn en local uniquement).

Un appel scrape_jobs() par couple (requête, site) : plus lent qu'un appel multi-sites,
mais isole les erreurs — un ban Indeed ne fait pas perdre les résultats Glassdoor.
"""
import os
import time
from datetime import date, datetime
from typing import Any

from loguru import logger

from job_hunter.models import RawJob, Source

QUERIES: list[tuple[str, str]] = [
    ("Service Delivery Manager", "Nantes, France"),
    ("Chef de projet IT", "Nantes, France"),
    ("Chef de projet digital", "Nantes, France"),
    ("PMO", "Nantes, France"),
    ("Product Manager", "Nantes, France"),
    ("Data Engineer", "Nantes, France"),
]

# Glassdoor retiré le 04/07/2026 : HTTP 400 "location not parsed" systématique côté JobSpy.
# Réactivation = le remettre ici (mapping SITE_TO_SOURCE conservé).
SITES_DEFAULT = ("indeed", "google")
SITE_TO_SOURCE: dict[str, Source] = {
    "indeed": "jobspy_indeed",
    "glassdoor": "jobspy_glassdoor",
    "google": "jobspy_google",
    "linkedin": "jobspy_linkedin",
}
RESULTS_WANTED = 50
HOURS_OLD = 48
RETRY_DELAYS = (1, 4)  # 3 tentatives max ; pas de 4e essai donc le 16 s du brief est sans objet
LINKEDIN_SPACING_S = 30


def collect(include_linkedin: bool = False) -> list[RawJob]:
    """Collecte toutes les requêtes sur tous les sites, dédupliquée intra-source par URL."""
    if include_linkedin and os.environ.get("GITHUB_ACTIONS"):
        raise RuntimeError("LinkedIn est interdit sur GitHub Actions (IP cloud = ban rapide)")

    sites = [*SITES_DEFAULT, *(["linkedin"] if include_linkedin else [])]
    jobs: dict[str, RawJob] = {}  # clé = job_url

    for query, location in QUERIES:
        for site in sites:
            rows = _scrape_with_retry(site, query, location)
            new = 0
            for row in rows:
                job = _to_raw_job(row)
                if job is not None and job.url not in jobs:
                    jobs[job.url] = job
                    new += 1
            logger.debug(f"jobspy[{site}] '{query}' : {len(rows)} lignes, {new} nouvelles")
            if site == "linkedin":
                time.sleep(LINKEDIN_SPACING_S)  # espacement imposé entre appels LinkedIn

    logger.info(f"jobspy : {len(jobs)} offres uniques ({', '.join(sites)})")
    return list(jobs.values())


def _scrape_with_retry(site: str, query: str, location: str) -> list[dict[str, Any]]:
    """Une recherche sur un site, 3 tentatives avec backoff. Échec total → [] (on continue)."""
    from jobspy import scrape_jobs  # import paresseux : lourd (pandas), inutile pour --help

    kwargs: dict[str, Any] = {
        "site_name": [site],
        "search_term": query,
        "location": location,
        "results_wanted": RESULTS_WANTED,
        "hours_old": HOURS_OLD,
        "country_indeed": "France",
        "verbose": 0,  # logs JobSpy silencieux, les nôtres suffisent
    }
    if site == "google":
        kwargs["google_search_term"] = f"{query} jobs near Nantes France since yesterday"

    for attempt in range(1, len(RETRY_DELAYS) + 2):
        try:
            df = scrape_jobs(**kwargs)
            return df.to_dict("records") if df is not None and not df.empty else []
        except Exception as exc:  # noqa: BLE001 — un site qui casse ne bloque pas le run
            logger.warning(f"jobspy[{site}] '{query}' : tentative {attempt} échouée : {exc}")
            if attempt <= len(RETRY_DELAYS):
                time.sleep(RETRY_DELAYS[attempt - 1])
    return []


def _to_raw_job(row: dict[str, Any]) -> RawJob | None:
    """Mappe une ligne du DataFrame JobSpy vers RawJob. None si inexploitable."""
    source = SITE_TO_SOURCE.get(_clean(row.get("site")) or "")
    url = _clean(row.get("job_url"))
    if source is None or url is None:
        return None
    interval = _clean(row.get("interval"))
    return RawJob(
        source=source,
        external_id=_clean(row.get("id")) or url,
        title=_clean(row.get("title")) or "(sans titre)",
        company=_clean(row.get("company")) or "Anonyme",
        location=_clean(row.get("location")) or "",
        contract_type=_contract_type(_clean(row.get("job_type"))),
        salary_min=_keur(row.get("min_amount"), interval),
        salary_max=_keur(row.get("max_amount"), interval),
        remote_pct=100 if row.get("is_remote") is True else None,
        description=_clean(row.get("description")),
        url=url,
        posted_at=_to_date(row.get("date_posted")),
        raw=_jsonable(row),
    )


# --- helpers ---------------------------------------------------------------


def _is_na(v: Any) -> bool:
    if v is None:
        return True
    try:
        return bool(v != v)  # attrape NaN et NaT sans importer pandas
    except Exception:  # noqa: BLE001 — objets exotiques : considérés non-NA
        return False


def _clean(v: Any) -> str | None:
    if _is_na(v):
        return None
    s = str(v).strip()
    return s or None


def _contract_type(job_type: str | None) -> str | None:
    # job_type JobSpy (fulltime/parttime) décrit la durée, pas le contrat FR → pas de "CDI" déduit
    if job_type is None:
        return None
    jt = job_type.lower()
    if "internship" in jt:
        return "Stage"
    if "temporary" in jt:
        return "Intérim"
    if "contract" in jt:
        return "Freelance"
    return None


def _keur(amount: Any, interval: str | None) -> int | None:
    """Montant → k€ annuel brut. Seuls yearly/monthly sont convertis (hourly : trop d'hypothèses)."""
    if _is_na(amount):
        return None
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return None
    match (interval or "").lower():
        case "yearly":
            return round(value / 1000)
        case "monthly":
            return round(value * 12 / 1000)
        case _:
            return None


def _to_date(v: Any) -> date | None:
    if _is_na(v):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    """Payload brut sérialisable (le DataFrame contient NaN, Timestamps, etc.)."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if _is_na(v):
            out[k] = None
        elif isinstance(v, (str, int, float, bool)):
            out[k] = v
        else:
            out[k] = str(v)
    return out
