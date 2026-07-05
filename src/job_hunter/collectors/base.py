"""Convention des collecteurs : un module = une fonction collect(...) -> list[RawJob].

Pas de Protocol ni de classe de base : les signatures varient (flags, chemins de
config) et le dispatch est explicite dans cli.py — une abstraction n'apporterait rien.
"""
import re
from collections.abc import Callable

from job_hunter.models import RawJob

CollectFn = Callable[..., list[RawJob]]

# Noms de sources acceptés par `run --sources` (ordre = ordre d'exécution)
SOURCES = ("jobspy", "france_travail", "apec", "careers_sites")

# --- Filtre contrat : on ne garde que le CDI (postes permanents) -------------
# Le champ contract_type n'est ni fiable ni homogène entre sources (codes FT, libellés
# Cegid, "permanent" SmartRecruiters, job_type jobspy…) → on croise le champ contrat ET
# l'intitulé, où « Stage / Alternance / Intérim » apparaît souvent quel que soit le champ.
_CONTRACT_EXCLUDED = {
    "cdd", "stage", "stagiaire", "alternance", "alternant", "apprentissage", "apprenti",
    "intérim", "interim", "mis", "mission", "freelance", "indépendant", "independant",
    "saisonnier", "contract", "temporary", "internship", "apprenticeship", "vie",
}
_TITLE_EXCLUDED_RE = re.compile(
    r"\b(stages?|stagiaires?|alternan\w+|apprenti\w*|int[eé]rims?|cdd)\b", re.IGNORECASE
)


def is_excluded_contract(job: RawJob) -> bool:
    """Vrai si l'offre est un CDD / stage / alternance / intérim / freelance (tout sauf
    CDI). Croise le type de contrat (mots) et l'intitulé."""
    ct = (job.contract_type or "").strip().lower()
    if ct and (ct in _CONTRACT_EXCLUDED or (set(ct.split()) & _CONTRACT_EXCLUDED)):
        return True
    return bool(_TITLE_EXCLUDED_RE.search(job.title))
