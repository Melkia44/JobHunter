"""Sous-score tier employeur : matching nom/aliases aux frontières de mots."""
import re

from job_hunter.models import Employer
from job_hunter.normalizer import normalize

_TIER_SCORES = {1: 100.0, 2: 80.0, 3: 60.0}
OUT_OF_LIST_SCORE = 50.0  # inconnu = neutre (même principe que location), pas une pénalité


def _word_match(needle: str, haystack: str) -> bool:
    # Frontières de mots, pas substring nu : "gan" ne doit pas matcher "morgan"
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


def find_employer(company: str, employers: list[Employer]) -> Employer | None:
    """Employeur cible correspondant à un nom de société, ou None. Premier match
    gagne (l'ordre du YAML fait autorité, ex. Sopra Banking avant Sopra Steria).
    Réutilisé par sheet_writer pour la mise à jour des statuts."""
    company_norm = normalize(company)
    if not company_norm:
        return None
    for emp in employers:
        candidates = [normalize(emp.name), *(normalize(a) for a in emp.aliases)]
        if any(
            _word_match(c, company_norm) or _word_match(company_norm, c)
            for c in candidates
            if c
        ):
            return emp
    return None


def score_tier(company: str, employers: list[Employer]) -> tuple[float, int | None]:
    """Retourne (score, tier) — tier None si hors-liste."""
    emp = find_employer(company, employers)
    if emp is None:
        return OUT_OF_LIST_SCORE, None
    return _TIER_SCORES[emp.tier], emp.tier
