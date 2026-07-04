"""Sous-score soft skills : regex pondérées sur la description (titre en fallback)."""
import re

SOFT_SKILLS: dict[str, int] = {
    r"\bmanagement\b": 20,
    r"\binternational\b": 15,
    r"\bmulti[- ]sites?\b": 15,
    r"\banglais\b": 15,
    r"\bencadrement\b": 10,
    r"\b(?:gouvernance|governance)\b": 10,
    r"\btransversal\b": 10,
    r"\brelation client\b": 10,
}


def score_soft_skills(text: str) -> float:
    low = text.lower()
    return float(min(100, sum(w for p, w in SOFT_SKILLS.items() if re.search(p, low))))
