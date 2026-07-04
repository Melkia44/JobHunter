"""Sous-score titre : substring (100) puis fuzzy difflib sur les titres cibles."""
from difflib import SequenceMatcher
from pathlib import Path

import yaml
from loguru import logger

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
]


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


def score_title_match(title: str, targets: list[str]) -> float:
    title_norm = normalize(title)
    for target in targets:
        if target in title_norm:
            return 100.0
    best = max(SequenceMatcher(None, title_norm, t).ratio() for t in targets)
    return round(best * 100, 1)
