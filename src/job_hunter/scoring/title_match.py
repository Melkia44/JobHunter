"""Sous-score titre : substring pondéré par la priorité du poste, puis fuzzy difflib.

Priorité de Mathieu (22/09/2026) : SDM > Product Manager > Data Engineer > Chef de projet IT.
Le score d'un titre = poids du titre cible le mieux placé qu'il contient (substring),
sinon ratio fuzzy × poids. « chef de projet » sans marqueur IT (« Chef de projet H/F »)
est plafonné plus bas : c'est la description, quand elle existe, qui tranche.
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
}
CHEF_DE_PROJET_GENERIC = 60


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
    if target == "chef de projet" and not _IT_MARKER_RE.search(title_norm):
        return CHEF_DE_PROJET_GENERIC
    return w


def score_title_match(title: str, targets: list[str]) -> float:
    title_norm = normalize(title)
    hits = [_weight(t, title_norm) for t in targets if t in title_norm]
    if hits:
        return float(max(hits))
    best = max(SequenceMatcher(None, title_norm, t).ratio() * _weight(t, title_norm) for t in targets)
    return round(best, 1)
