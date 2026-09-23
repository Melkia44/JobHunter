"""Sous-score titre : substring pondéré par la priorité du poste, puis fuzzy difflib.

Priorité de Mathieu (22/09/2026) : SDM > Product Manager > Data Engineer > Chef de projet IT.
Le score d'un titre = poids du titre cible le mieux placé qu'il contient (substring),
sinon ratio fuzzy × poids. Les intitulés ambigus (GATED_TARGETS : « chef de projet »,
« service manager », « responsable de production »…) sans marqueur IT sont plafonnés plus bas : c'est la description, quand elle existe, qui tranche.
"""
from difflib import SequenceMatcher
from pathlib import Path

import yaml
from loguru import logger

from job_hunter.collectors.base import _IT_MARKER_RE
from job_hunter.normalizer import normalize

# Fallback si data/target_titles.yaml est vide/illisible. "chef de projet" couvre
# les variantes informatique/digital/MOE par substring — pas de doublons inutiles.
DEFAULT_TARGET_TITLES = [
    "service delivery manager",
    "delivery manager",
    "sdm",
    "chef de projet",
    "responsable operations services",
    "operations services",
    "pmo",
    "product manager",
    "product owner",
    "data engineer",
    "chef de projet delivery",
    # Élargissement 23/09/2026 : synonymes du métier SDM / run / ITSM
    "service delivery",
    "delivery lead",
    "service owner",
    "service manager",
    "responsable mco",
    "responsable run",
    "incident manager",
    "problem manager",
    "itsm",
    "technical account manager",
    "responsable de production",
    "responsable production",
    "responsable d'exploitation",
    "responsable exploitation",
    "contract manager",
    "customer success manager",
    "project manager",
]

# Poids par titre cible normalisé (absent = 100). Modifier ici pour changer les priorités.
TITLE_WEIGHTS: dict[str, float] = {
    "service delivery manager": 100,
    "delivery manager": 100,
    "sdm": 100,
    "responsable operations services": 100,
    "operations services": 100,
    "product manager": 90,
    "product owner": 90,
    "chef de projet delivery": 90,
    "data engineer": 85,
    "chef de projet": 75,  # avec marqueur IT ; sinon CHEF_DE_PROJET_GENERIC
    "pmo": 75,
    "service delivery": 100,
    "delivery lead": 95,
    "service owner": 95,
    "service manager": 95,  # gated
    "responsable mco": 95,
    "responsable run": 95,
    "incident manager": 85,
    "problem manager": 80,
    "itsm": 85,
    "technical account manager": 85,
    "responsable de production": 90,  # gated
    "responsable production": 90,  # gated
    "responsable d'exploitation": 90,  # gated
    "responsable exploitation": 90,  # gated
    "contract manager": 80,  # gated
    "customer success manager": 70,  # gated
    "project manager": 75,  # gated, comme « chef de projet »
}
CHEF_DE_PROJET_GENERIC = 60

# Intitulés ambigus hors IT (industrie, logistique, service client, juridique…) :
# sans marqueur IT dans le titre, plafonnés à CHEF_DE_PROJET_GENERIC.
GATED_TARGETS = {
    "chef de projet",
    "service manager",
    "responsable de production",
    "responsable production",
    "responsable d'exploitation",
    "responsable exploitation",
    "contract manager",
    "customer success manager",
    "project manager",
}


def load_target_titles(path: Path) -> list[str]:
    """Titres cibles normalisés depuis le YAML ; fallback liste intégrée."""
    titles: list[str] = []
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            titles = [normalize(t) for t in (data.get("titles") or []) if isinstance(t, str)]
        except yaml.YAMLError as exc:
            logger.warning(f"target_titles.yaml malformé ({exc}), fallback liste intégrée")
    if not titles:
        titles = [normalize(t) for t in DEFAULT_TARGET_TITLES]
    return titles


def _weight(target: str, title_norm: str) -> float:
    w = TITLE_WEIGHTS.get(target, 100)
    if target in GATED_TARGETS and not _IT_MARKER_RE.search(title_norm):
        return CHEF_DE_PROJET_GENERIC
    return w


def score_title_match(title: str, targets: list[str]) -> float:
    title_norm = normalize(title)
    hits = [_weight(t, title_norm) for t in targets if t in title_norm]
    if hits:
        return float(max(hits))
    # Fuzzy sans marqueur IT plafonné comme les intitulés ambigus : « Responsable brasserie »
    # (≈ « responsable run ») ou « Responsable de magasin » montaient à 67-74 (run #90, 23/09/2026).
    cap = 100 if _IT_MARKER_RE.search(title_norm) else CHEF_DE_PROJET_GENERIC
    best = max(
        SequenceMatcher(None, title_norm, t).ratio() * min(_weight(t, title_norm), cap) for t in targets
    )
    return round(best, 1)
