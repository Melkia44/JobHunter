"""Collecteur APEC — API de recherche JSON publique (sans auth).

Le RSS des recherches sauvegardées a disparu (APEC est une SPA qui ne propose plus
que l'alerte email — constaté 2026-07-05). On interroge donc l'endpoint JSON que le
site appelle lui-même : POST cms/webservices/rechercheOffre. Bien plus riche que le
RSS (entreprise, lieu, salaire, description, URL directe).

Filtre géographique côté client : distance au centre du profil (lat/lon de l'offre) ≤
radius_km, repli sur location.departments si l'offre n'est pas géolocalisée. L'API filtre
par code géo interne qu'on ne récupère pas (autocomplétion protégée DataDome).
Mots-clés : profile.yaml › search.apec_keywords.

Nom de fichier conservé (apec_rss_collector) pour ne pas toucher au dispatch cli.py ;
le RSS n'est plus utilisé. source='apec_rss' inchangé (mappings modèles/Sheet).

Limite connue : APEC est derrière DataDome. Le POST passe depuis une IP résidentielle ;
depuis l'IP datacenter de GitHub Actions il peut être challengé (comme LinkedIn). Si
bloqué, la collecte échoue proprement (source isolée) et l'onglet Sources la marque NOK.
"""
import math
import re
import time
from datetime import date
from typing import Any

import httpx
from loguru import logger

from job_hunter.config import Settings
from job_hunter.models import RawJob
from job_hunter.profile import get_profile

SEARCH_URL = "https://www.apec.fr/cms/webservices/rechercheOffre"
DETAIL_URL = "https://www.apec.fr/candidat/recherche-emploi.html/emploi/detail-offre/{}"

CDI_CODE = "101888"          # CONTRACT_TYPE_FILTERING : CDI
PAGE_SIZE = 100
MAX_PAGES = 2                # 200 offres récentes / mot-clé, triées par date
TIMEOUT_S = 20.0
RETRY_DELAYS = (1, 4)
_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0",
}
_DEPT_RE = re.compile(r"(\d{2,3}|2[AB])\s*$")  # « Nantes - 44 » → 44


def collect(settings: Settings) -> list[RawJob]:  # noqa: ARG001 — signature homogène
    """Collecte paginée multi-mots-clés, filtrée sur la zone, dédupliquée par n° d'offre."""
    offers: dict[str, RawJob] = {}
    with httpx.Client(timeout=TIMEOUT_S, headers=_HEADERS) as client:
        for kw in get_profile().search.apec_keywords:
            for page in range(MAX_PAGES):
                data = _search(client, kw, page)
                if data is None:
                    break
                resultats = data.get("resultats") or []
                for offer in resultats:
                    job = _to_raw_job(offer)
                    if job is not None and job.external_id not in offers:
                        offers[job.external_id] = job
                if (page + 1) * PAGE_SIZE >= data.get("totalCount", 0) or not resultats:
                    break

    logger.info(f"apec : {len(offers)} offres uniques (≤ {get_profile().location.radius_km} km de {get_profile().location.center.city}, CDI)")
    return list(offers.values())


def _search(client: httpx.Client, keyword: str, page: int) -> dict[str, Any] | None:
    """POST recherche avec retry sur 5xx/429/timeout. None si échec ou réponse non-JSON
    (DataDome renvoie du HTML → on abandonne ce mot-clé sans planter)."""
    payload = {
        "motsCles": keyword,
        "typesContrat": [CDI_CODE],
        "pagination": {"startIndex": page * PAGE_SIZE, "range": PAGE_SIZE},
        "sorts": [{"type": "DATE", "direction": "DESCENDING"}],
        "activeFiltre": True,
    }
    for attempt in range(1, len(RETRY_DELAYS) + 2):
        try:
            resp = client.post(SEARCH_URL, json=payload)
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError:  # HTML (DataDome) au lieu de JSON
                    logger.warning(f"apec[{keyword}] : réponse non-JSON (anti-bot ?), skip")
                    return None
            if resp.status_code == 429 or resp.status_code >= 500:
                raise httpx.HTTPStatusError("retry", request=resp.request, response=resp)
            logger.warning(f"apec[{keyword}] : HTTP {resp.status_code}, skip")
            return None
        except httpx.HTTPError as exc:
            if attempt <= len(RETRY_DELAYS):
                time.sleep(RETRY_DELAYS[attempt - 1])
            else:
                logger.warning(f"apec[{keyword}] : {exc}, skip")
    return None


def _to_raw_job(offer: dict[str, Any]) -> RawJob | None:
    num = offer.get("numeroOffre") or offer.get("id")
    title = (offer.get("intitule") or "").strip()
    lieu = (offer.get("lieuTexte") or "").strip()
    if not num or not title or not _in_zone(offer):
        return None
    confidentiel = offer.get("offreConfidentielle") or str(offer.get("nomCommercial", "")).startswith("ZZ_")
    company = "Anonyme (APEC)" if confidentiel else (offer.get("nomCommercial") or "Anonyme (APEC)").strip()
    smin, smax = _parse_salary(offer.get("salaireTexte") or "")
    return RawJob(
        source="apec_rss",
        external_id=str(num),
        title=title,
        company=company,
        location=lieu,
        contract_type="CDI",  # on filtre typesContrat=CDI à la source
        salary_min=smin,
        salary_max=smax,
        remote_pct=None,
        description=(offer.get("texteOffre") or "").strip() or None,
        url=DETAIL_URL.format(num),
        posted_at=_parse_date(offer.get("datePublication")),
        raw=offer,
    )


def _in_zone(offer: dict[str, Any]) -> bool:
    """Vrai si l'offre est à ≤ radius_km du centre du profil (haversine sur lat/lon de
    l'API). Repli sur location.departments si l'offre n'est pas géolocalisée."""
    zone = get_profile().location
    lat, lon = offer.get("latitude"), offer.get("longitude")
    if offer.get("localisable") and lat is not None and lon is not None:
        try:  # l'API renvoie lat/lon en chaînes
            return _haversine_km((zone.center.lat, zone.center.lon), (float(lat), float(lon))) <= zone.radius_km
        except (TypeError, ValueError):
            pass
    m = _DEPT_RE.search(offer.get("lieuTexte") or "")
    return bool(m) and m.group(1) in zone.departments


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    (la1, lo1), (la2, lo2) = a, b
    dla, dlo = math.radians(la2 - la1), math.radians(lo2 - lo1)
    h = math.sin(dla / 2) ** 2 + math.cos(math.radians(la1)) * math.cos(math.radians(la2)) * math.sin(dlo / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _parse_salary(texte: str) -> tuple[int | None, int | None]:
    """« 43 - 53 k€ brut annuel » → (43, 53). Montants déjà en k€ dans le texte APEC.
    Filtre 15-200 k€ pour écarter les artefacts. « Selon profil » → (None, None)."""
    if "k€" not in texte.lower() and "keur" not in texte.lower():
        return None, None
    nums = [round(float(n.replace(",", "."))) for n in _NUM_RE.findall(texte)]
    keur = [v for v in nums if 15 <= v <= 200]
    if not keur:
        return None, None
    if len(keur) == 1:
        return keur[0], None
    return min(keur[:2]), max(keur[:2])


def _parse_date(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])  # « 2026-07-04T15:38:29.000+0000 » → 2026-07-04
    except (TypeError, ValueError):
        return None
