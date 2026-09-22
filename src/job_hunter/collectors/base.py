"""Convention des collecteurs : un module = une fonction collect(...) -> list[RawJob].

Pas de Protocol ni de classe de base : les signatures varient (flags, chemins de
config) et le dispatch est explicite dans cli.py — une abstraction n'apporterait rien.
"""
import re
from collections.abc import Callable

from job_hunter.models import RawJob
from job_hunter.normalizer import normalize

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


# --- Filtre domaine : écarte les métiers hors IT ------------------------------
# « chef de projet » matche n'importe quel domaine (title_match = 100 par substring) :
# relevé du 22/09/2026 → 193/399 « chef de projet » sans rapport avec l'IT (CVC, fluides,
# HSE, BTP, R&D fromagerie…), dont 129 via Indeed. Liste noire sur l'intitulé normalisé
# (sans accents) plutôt que liste blanche IT : les titres IT légitimes sont trop variés
# (ServiceNow, EPM, Scrum, IA…) pour une liste blanche fiable.
# Pluriel « environnements » volontairement non couvert (« Environnements Cloud » = IT).
_OFF_DOMAIN_RE = re.compile(
    r"\b("
    r"industriali\w*|industriel\w*|industrie|usine|production industrielle|"
    r"cvc|fluides?|tce|menuiserie|plomberie|electricite|chauffage|genie (civil|climatique)|"
    r"btp|batiments?|chantiers?|travaux|voirie|vrd|assainissement|hydraulique|"
    r"maitrise d.oeuvre|opc|immobilier|urbanisme|amenagement|paysag\w*|ascenseurs?|"
    r"photovoltai\w*|eolien\w*|environnement|environnemental\w*|hse|qhse|qse|ecologue|"
    r"botaniste|agronom\w*|agroalimentaire|fromagerie|piping|pipe|naval\w*|"
    r"communication|marketing|evenementiel\w*|administration des ventes|adv|achats?|"
    r"ressources humaines|rh|recrutement|chef de chantier|conducteur de travaux"
    r")\b"
)

# Marqueurs IT explicites : ils sauvent un intitulé sinon écarté (« Chef de projet IT
# ERP agroalimentaire », « Chef de projet SIRH », « Data Engineer Marketing »…).
# « digital » seul volontairement absent : « Chargé(e) marketing digital » reste écarté.
_IT_MARKER_RE = re.compile(
    r"\b("
    r"it|si|sirh|informati\w*|numerique|transformation digitale|data|big data|erp|crm|sap|"
    r"moe|moa|amoa|cyber\w*|systemes?|reseaux?|telecoms?|logiciels?|software|developpeur|"
    r"developer|angular|cloud|devops|product owner|business analyst|e-commerce"
    r")\b"
)


def is_off_domain(job: RawJob) -> bool:
    """Vrai si l'intitulé relève d'un métier hors IT (BTP, industrie, HSE, marketing…)
    ET ne porte aucun marqueur IT explicite."""
    title = normalize(job.title)
    return bool(_OFF_DOMAIN_RE.search(title)) and not _IT_MARKER_RE.search(title)


def is_excluded_contract(job: RawJob) -> bool:
    """Vrai si l'offre est un CDD / stage / alternance / intérim / freelance (tout sauf
    CDI). Croise le type de contrat (mots) et l'intitulé."""
    ct = (job.contract_type or "").strip().lower()
    if ct and (ct in _CONTRACT_EXCLUDED or (set(ct.split()) & _CONTRACT_EXCLUDED)):
        return True
    return bool(_TITLE_EXCLUDED_RE.search(job.title))
