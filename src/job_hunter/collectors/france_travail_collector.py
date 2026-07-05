"""Collecteur France Travail — API Offres d'emploi v2 (OAuth2 client_credentials).

Filtre géographique : Nantes (44109) + 50 km. Limitation actée : les offres full
remote publiées hors 44 n'arrivent pas par cette source (couvertes par les autres).
"""
import re
import time
from datetime import date
from typing import Any

import httpx
from loguru import logger

from job_hunter.config import Settings
from job_hunter.models import RawJob

TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token"
SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
SCOPE = "api_offresdemploiv2 o2dsoffre"

# MOA/PMO, support-SDM, études & dev (PO tech / DE), data engineering.
# Écartés : M1803 (DSI, trop senior), M1808 (réseau/infra), E1105 (édition de livres).
ROME_CODES = "M1806,M1802,M1805,M1811"
COMMUNE_NANTES = "44109"
DISTANCE_KM = 50
PUBLIEE_DEPUIS = 3   # 72 h : couvre un run raté ; la dédup (Phase 3) absorbe les répétitions
PAGE_SIZE = 150      # max autorisé par l'API
MAX_PAGES = 10       # garde-fou pagination (1 500 offres — jamais atteint en pratique)
RETRY_DELAYS = (1, 4)
TIMEOUT_S = 15.0

# Cache process : un run dure quelques minutes, le TTL 24 h du token est sans objet
_token_cache: dict[str, Any] = {"token": None, "expires_at": 0.0}


def collect(settings: Settings) -> list[RawJob]:
    """Collecte paginée, dédupliquée intra-source par id d'offre."""
    if not settings.france_travail_client_id or not settings.france_travail_client_secret:
        raise RuntimeError(
            "FRANCE_TRAVAIL_CLIENT_ID / FRANCE_TRAVAIL_CLIENT_SECRET manquants dans .env "
            "(app à créer sur francetravail.io — voir README)"
        )

    token = _get_token(settings.france_travail_client_id, settings.france_travail_client_secret)
    params: dict[str, Any] = {
        "codeROME": ROME_CODES,
        "commune": COMMUNE_NANTES,
        "distance": DISTANCE_KM,
        "publieeDepuis": PUBLIEE_DEPUIS,
        "sort": 1,  # date de publication décroissante
        # Filtre serveur : CDI uniquement (postes permanents) — écarte CDD, MIS (intérim),
        # FRA (franchise)… à la source. Le filtre central base.is_excluded_contract couvre
        # les autres sources et l'intitulé (stage/alternance).
        "typeContrat": "CDI",
    }
    offers: dict[str, RawJob] = {}

    with httpx.Client(timeout=TIMEOUT_S, headers={"Authorization": f"Bearer {token}"}) as client:
        for page in range(MAX_PAGES):
            start = page * PAGE_SIZE
            resp = _get_with_retry(client, {**params, "range": f"{start}-{start + PAGE_SIZE - 1}"})
            if resp is None or resp.status_code == 204:  # 204 = aucun résultat
                break
            for offer in resp.json().get("resultats", []):
                job = _to_raw_job(offer)
                if job is not None and job.external_id not in offers:
                    offers[job.external_id] = job
            if not _has_more(resp):
                break

    logger.info(f"france_travail : {len(offers)} offres (ROME {ROME_CODES}, {DISTANCE_KM} km)")
    return list(offers.values())


def _get_token(client_id: str, client_secret: str) -> str:
    now = time.monotonic()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]
    resp = httpx.post(
        TOKEN_URL,
        params={"realm": "/partenaire"},
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": SCOPE,
        },
        timeout=TIMEOUT_S,
    )
    resp.raise_for_status()
    payload = resp.json()
    _token_cache["token"] = payload["access_token"]
    _token_cache["expires_at"] = now + float(payload.get("expires_in", 1500)) - 60  # marge 60 s
    return _token_cache["token"]


def _get_with_retry(client: httpx.Client, params: dict[str, Any]) -> httpx.Response | None:
    """GET avec 3 tentatives sur 5xx/429/timeout. Les autres 4xx ne se retryent pas."""
    for attempt in range(1, len(RETRY_DELAYS) + 2):
        try:
            resp = client.get(SEARCH_URL, params=params)
            if resp.status_code in (200, 206, 204):  # 206 = réponse paginée normale
                return resp
            if resp.status_code == 429 or resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp
                )
            logger.error(f"france_travail : HTTP {resp.status_code} — {resp.text[:200]}")
            return None
        except httpx.HTTPError as exc:
            logger.warning(f"france_travail : tentative {attempt} échouée : {exc}")
            if attempt <= len(RETRY_DELAYS):
                time.sleep(RETRY_DELAYS[attempt - 1])
    return None


def _has_more(resp: httpx.Response) -> bool:
    # Content-Range : "offres 0-149/321"
    m = re.search(r"(\d+)-(\d+)/(\d+)", resp.headers.get("Content-Range", ""))
    return bool(m) and int(m.group(2)) + 1 < int(m.group(3))


def _to_raw_job(offer: dict[str, Any]) -> RawJob | None:
    oid = offer.get("id")
    url = offer.get("origineOffre", {}).get("urlOrigine") or (
        f"https://candidat.francetravail.fr/offres/recherche/detail/{oid}" if oid else None
    )
    if not oid or not url:
        return None
    smin, smax = _parse_salary(offer.get("salaire", {}).get("libelle") or "")
    # Règle du brief ; en pratique FT marque rarement le télétravail dans ce champ → souvent None
    duree = (offer.get("dureeTravailLibelleConverti") or "").lower()
    return RawJob(
        source="france_travail",
        external_id=str(oid),
        title=offer.get("intitule") or "(sans titre)",
        company=(offer.get("entreprise") or {}).get("nom") or "Anonyme",
        location=(offer.get("lieuTravail") or {}).get("libelle") or "",
        contract_type=offer.get("typeContrat"),
        salary_min=smin,
        salary_max=smax,
        remote_pct=100 if "teletravail" in duree or "télétravail" in duree else None,
        description=offer.get("description"),
        url=url,
        posted_at=_parse_date(offer.get("dateCreation")),
        raw=offer,
    )


_NUM_RE = re.compile(r"\d[\d\s ]*(?:[.,]\d+)?")


def _parse_salary(libelle: str) -> tuple[int | None, int | None]:
    """'Annuel de 55000,00 Euros à 65000,00 Euros sur 12 mois' → (55, 65).

    Le filtre 15-200 k€ écarte les artefacts ('12 mois', primes) et les montants
    aberrants. Horaire : non converti, trop d'hypothèses.
    """
    low = libelle.lower()
    if "annuel" in low:
        factor = 1.0
    elif "mensuel" in low:
        factor = 12.0
    else:
        return None, None
    nums = [
        float(n.replace(" ", "").replace(" ", "").replace(",", "."))
        for n in _NUM_RE.findall(libelle)
    ]
    keur = [v for v in (round(n * factor / 1000) for n in nums) if 15 <= v <= 200]
    if not keur:
        return None, None
    if len(keur) == 1:
        return keur[0], None
    return min(keur[:2]), max(keur[:2])


def _parse_date(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None
