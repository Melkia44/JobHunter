"""Sous-score localisation.

Principe (acté 04/07/2026) : on ne pénalise que ce qu'on sait. L'inconnu d'une
source géo-filtrée par construction (FT commune+50 km, recherches APEC, employeurs
choisis) vaut 75 ; l'inconnu jobspy vaut 50 (neutre). Plus aucun 0 : le hors-zone
positif n'est pas détectable sans gazetteer national, on s'appuie sur les
pré-filtres amont.
"""
import re

from job_hunter.models import Source
from job_hunter.normalizer import normalize

# Formes normalisées tout-tirets (le matching remplace les espaces par des tirets)
NANTES_METRO = {
    "nantes", "saint-herblain", "reze", "vertou", "carquefou", "orvault",
    "saint-sebastien", "bouguenais", "la-chapelle-sur-erdre",
}
LOIRE_ATLANTIQUE_50KM = {
    "ancenis", "clisson", "chateaubriant", "saint-nazaire", "pornic",
    "nort-sur-erdre", "blain", "guerande",
}
GEO_PREFILTERED: set[str] = {"france_travail", "apec_rss", "careers_site"}
_DEPT44_RE = re.compile(r"^44\s*-|\b44\d{3}\b")
_CP_RE = re.compile(r"\b(\d{5})\b")


def score_location(location: str, remote_pct: int | None, source: Source) -> float:
    if remote_pct == 100:
        return 100.0
    loc = normalize(location or "")
    loc_h = loc.replace(" ", "-")  # "saint herblain" et "saint-herblain" convergent
    if "nort-sur-erdre" in loc_h:
        return 100.0
    if any(city in loc_h for city in NANTES_METRO):
        return 90.0
    if any(city in loc_h for city in LOIRE_ATLANTIQUE_50KM):
        return 75.0
    if _DEPT44_RE.search(loc) or "loire-atlantique" in loc_h:
        return 60.0
    m = _CP_RE.search(loc)
    if m and not m.group(1).startswith("44"):
        # CP français explicite hors 44 (ex. lieu extrait d'une page détail Cegid) :
        # positivement hors zone — le plancher géo-filtré ne doit pas s'appliquer
        return 30.0
    if remote_pct is not None and remote_pct >= 50:
        return 70.0
    if source in GEO_PREFILTERED:
        return 75.0  # en zone par construction, ville juste hors gazetteer
    return 50.0  # jobspy, lieu inconnu : neutre
