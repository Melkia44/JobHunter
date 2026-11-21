"""Sous-score soft skills : regex pondérées (profile.yaml › soft_skills) sur titre + description."""
import re

from job_hunter.profile import get_profile


def score_soft_skills(text: str) -> float:
    low = text.lower()
    skills = get_profile().soft_skills
    return float(min(100, sum(w for p, w in skills.items() if re.search(p, low))))
