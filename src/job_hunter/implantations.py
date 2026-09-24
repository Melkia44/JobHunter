"""Veille implantations : nouveaux établissements secondaires en Loire-Atlantique
d'entreprises déjà significatives, via l'API Sirene (INSEE).

Requête volontairement large côté API (département + date de création + non-siège),
filtrage fin côté client (effectif unité légale, NAF) : la syntaxe multicritère
Sirene sur les variables historisées est capricieuse, et le volume reste faible
(quelques centaines d'établissements par mois).

Clé API : portail-api.insee.fr → application → souscrire « API Sirene » (gratuit).
"""
import math
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx
import yaml
from loguru import logger

SIRENE_URL = "https://api.insee.fr/api-sirene/3.11/siret"
PAGE_SIZE = 1000
ANNUAIRE_URL = "https://annuaire-entreprises.data.gouv.fr/etablissement/{siret}"

TRANCHES_LABELS = {
    "21": "50-99", "22": "100-199", "31": "200-249", "32": "250-499",
    "41": "500-999", "42": "1000-1999", "51": "2000-4999", "52": "5000-9999", "53": "10000+",
}


@dataclass(frozen=True)
class ImplantationConfig:
    departement: str
    tranches: frozenset[str]
    categories: frozenset[str]
    naf_prefixes: dict[str, str]
    nantes_xy: tuple[float, float]


@dataclass
class Implantation:
    siret: str
    siren: str
    entreprise: str
    enseigne: str
    commune: str
    code_postal: str
    date_creation: str
    naf: str
    secteur: str
    effectif_groupe: str
    categorie: str
    km_nantes: int | None

    @property
    def url(self) -> str:
        return ANNUAIRE_URL.format(siret=self.siret)


def load_config(path: Path) -> ImplantationConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return ImplantationConfig(
        departement=str(raw["departement"]),
        tranches=frozenset(raw["tranches_effectif_min"]),
        categories=frozenset(raw.get("categories_entreprise", [])),
        naf_prefixes={str(k): v for k, v in raw["naf_prefixes"].items()},
        nantes_xy=tuple(raw["nantes_lambert93"]),
    )


def build_query(departement: str, since: date, until: date) -> str:
    return (
        f"codePostalEtablissement:{departement}* "
        f"AND dateCreationEtablissement:[{since.isoformat()} TO {until.isoformat()}] "
        f"AND etablissementSiege:false"
    )


def fetch(api_key: str, departement: str, since: date, until: date) -> list[dict]:
    """Tous les établissements bruts de la fenêtre (pagination par curseur)."""
    if not api_key:
        raise RuntimeError("INSEE_API_KEY manquant (.env ou secret GHA)")
    headers = {"X-INSEE-Api-Key-Integration": api_key, "Accept": "application/json"}
    params = {"q": build_query(departement, since, until), "nombre": PAGE_SIZE, "curseur": "*"}
    out: list[dict] = []
    with httpx.Client(timeout=60, headers=headers) as client:
        while True:
            resp = client.get(SIRENE_URL, params=params)
            if resp.status_code == 404:  # Sirene répond 404 quand aucun résultat
                break
            if resp.status_code == 429:  # quota 30 req/min
                time.sleep(5)
                continue
            resp.raise_for_status()
            body = resp.json()
            out.extend(body.get("etablissements", []))
            header = body.get("header", {})
            nxt = header.get("curseurSuivant")
            if not nxt or nxt == params["curseur"]:
                break
            params["curseur"] = nxt
    logger.info(f"Sirene : {len(out)} établissement(s) créé(s) dans le {departement} depuis {since}")
    return out


def _current_period(etab: dict) -> dict:
    periods = etab.get("periodesEtablissement") or [{}]
    return next((p for p in periods if p.get("dateFin") is None), periods[0])


def _match_naf(codes: list[str], prefixes: dict[str, str]) -> tuple[str, str] | None:
    """(code NAF retenu, libellé secteur) — préfixe le plus long gagne."""
    for code in codes:
        if not code:
            continue
        hits = [p for p in prefixes if code.startswith(p)]
        if hits:
            best = max(hits, key=len)
            return code, prefixes[best]
    return None


def _km(adr: dict, ref: tuple[float, float]) -> int | None:
    try:
        x = float(adr["coordonneeLambertAbscisseEtablissement"])
        y = float(adr["coordonneeLambertOrdonneeEtablissement"])
    except (KeyError, TypeError, ValueError):
        return None
    return round(math.hypot(x - ref[0], y - ref[1]) / 1000)


def select(etabs: list[dict], cfg: ImplantationConfig) -> list[Implantation]:
    """Filtre : actif + unité légale ≥ seuil d'effectif (ou ETI/GE) + NAF en liste blanche."""
    kept: list[Implantation] = []
    for e in etabs:
        ul = e.get("uniteLegale") or {}
        period = _current_period(e)
        if period.get("etatAdministratifEtablissement", "A") != "A":
            continue
        tranche = ul.get("trancheEffectifsUniteLegale") or ""
        categorie = ul.get("categorieEntrepriseUniteLegale") or ""
        if tranche not in cfg.tranches and categorie not in cfg.categories:
            continue
        naf = _match_naf(
            [period.get("activitePrincipaleEtablissement"), ul.get("activitePrincipaleUniteLegale")],
            cfg.naf_prefixes,
        )
        if naf is None:
            continue
        adr = e.get("adresseEtablissement") or {}
        name = (
            ul.get("denominationUniteLegale")
            or ul.get("denominationUsuelle1UniteLegale")
            or f"{ul.get('prenom1UniteLegale', '')} {ul.get('nomUniteLegale', '')}".strip()
        )
        kept.append(
            Implantation(
                siret=e["siret"],
                siren=e.get("siren", e["siret"][:9]),
                entreprise=name,
                enseigne=period.get("enseigne1Etablissement") or period.get("denominationUsuelleEtablissement") or "",
                commune=adr.get("libelleCommuneEtablissement") or "",
                code_postal=adr.get("codePostalEtablissement") or "",
                date_creation=e.get("dateCreationEtablissement") or "",
                naf=naf[0],
                secteur=naf[1],
                effectif_groupe=TRANCHES_LABELS.get(tranche, "NC"),
                categorie=categorie,
                km_nantes=_km(adr, cfg.nantes_xy),
            )
        )
    kept.sort(key=lambda i: i.date_creation, reverse=True)
    return kept
