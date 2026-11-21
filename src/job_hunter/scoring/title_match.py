"""Sous-score titre : substring pondéré par la priorité du poste, puis fuzzy difflib.

Intitulés visés, poids et intitulés ambigus (gated) : profile.yaml › titles.
Le score d'un titre = poids de l'intitulé visé le mieux placé qu'il contient (substring),
sinon ratio fuzzy × poids. Si filters.it_only : les intitulés gated sans marqueur IT,
et le fuzzy sans marqueur IT, sont plafonnés à generic_cap.
"""
import re
from difflib import SequenceMatcher

from job_hunter.collectors.base import _IT_MARKER_RE
from job_hunter.normalizer import normalize
from job_hunter.profile import get_profile


def _weight(target: str, title_norm: str) -> float:
    prof = get_profile()
    rule = prof.title_rule(target)
    w = rule.weight if rule else 100
    if rule and rule.gated and prof.filters.it_only and not _IT_MARKER_RE.search(title_norm):
        return prof.generic_cap
    return w


def _contains(target: str, title_norm: str) -> bool:
    """Substring, sauf sigles courts (« roc », « sdm », « pmo », « itsm ») : mot entier,
    sinon « roc » matcherait « process », « procurement », « brocante »…"""
    if len(target) <= 4:
        return re.search(rf"\b{re.escape(target)}\b", title_norm) is not None
    return target in title_norm


def score_title_match(title: str, targets: list[str]) -> float:
    title_norm = normalize(title)
    hits = [_weight(t, title_norm) for t in targets if _contains(t, title_norm)]
    if hits:
        return float(max(hits))
    if not targets:
        return 0.0
    # Fuzzy sans marqueur IT plafonné comme les intitulés ambigus : « Responsable brasserie »
    # (≈ « responsable run ») ou « Responsable de magasin » montaient à 67-74 (run #90, 23/09/2026).
    prof = get_profile()
    it_ok = not prof.filters.it_only or _IT_MARKER_RE.search(title_norm)
    cap = 100 if it_ok else prof.generic_cap
    best = max(
        SequenceMatcher(None, title_norm, t).ratio() * min(_weight(t, title_norm), cap) for t in targets
    )
    return round(best, 1)
