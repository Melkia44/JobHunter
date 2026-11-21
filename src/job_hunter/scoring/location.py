"""Sous-score localisation (zone : profile.yaml › location).

Principe (acté 04/07/2026) : on ne pénalise que ce qu'on sait. L'inconnu d'une
source géo-filtrée par construction (FT centre+rayon, APEC, employeurs choisis)
vaut 75 ; l'inconnu jobspy vaut 50 (neutre). Le hors-zone n'est détecté que sur
un code postal explicite hors des départements de la zone (30).

  home 100 · core_cities 90 · area_cities 75 · departments 60 · CP hors zone 30
"""
import re

from job_hunter.models import Source
from job_hunter.normalizer import normalize
from job_hunter.profile import get_profile

GEO_PREFILTERED: set[str] = {"france_travail", "apec_rss", "careers_site"}
_CP_RE = re.compile(r"\b(\d{5})\b")


def _hyph(s: str) -> str:
    return normalize(s).replace(" ", "-")  # "saint herblain" et "saint-herblain" convergent


def _in_department(loc: str, loc_h: str, depts: list[str], names: list[str]) -> bool:
    for d in depts:
        if re.search(rf"^{re.escape(d)}\s*-|\b{re.escape(d)}\d{{{5 - len(d)}}}\b", loc):
            return True
    return any(_hyph(n) in loc_h for n in names)


def score_location(location: str, remote_pct: int | None, source: Source) -> float:
    if remote_pct == 100:
        return 100.0
    zone = get_profile().location
    loc = normalize(location or "")
    loc_h = loc.replace(" ", "-")
    if "(hors zone)" in loc:
        return 30.0  # lieu explicite hors zone (page détail) : cf. _zone_tagged
    if zone.home and _hyph(zone.home) in loc_h:
        return 100.0
    if any(_hyph(c) in loc_h for c in [zone.center.city, *zone.core_cities]):
        return 90.0
    if any(_hyph(c) in loc_h for c in zone.area_cities):
        return 75.0
    if _in_department(loc, loc_h, zone.departments, zone.department_names):
        return 60.0
    m = _CP_RE.search(loc)
    if m and zone.departments and not m.group(1).startswith(tuple(zone.departments)):
        # CP français explicite hors zone (ex. lieu extrait d'une page détail Cegid) :
        # positivement hors zone — le plancher géo-filtré ne doit pas s'appliquer
        return 30.0
    if remote_pct is not None and remote_pct >= 50:
        return 70.0
    if source in GEO_PREFILTERED:
        return 75.0  # en zone par construction, ville juste hors liste
    return 50.0  # jobspy, lieu inconnu : neutre


def is_known_zone(location: str) -> bool:
    """Vrai si le lieu est reconnu dans la zone (villes listées ou département)."""
    return score_location(location, None, "jobspy_indeed") >= 60.0
