"""Sous-score hard skills : regex pondérées sur la description (titre en fallback)."""
import re

HARD_SKILLS: dict[str, int] = {
    # Delivery & pilotage
    r"\bitil\b": 15,
    r"\bdelivery\b": 15,
    r"\binfog[eé]rance\b": 15,
    r"\bsla\b": 10,
    r"\bkpi\b": 8,
    r"\bincident\b": 8,
    r"\bcopil\b": 5,
    # Télécom
    r"\bt[eé]l[eé]com\b": 10,
    r"\bb2b\b": 8,
    r"\b(?:xdsl|thd|lte|4g|5g)\b": 5,  # alternation groupée (le brief ancrait mal les \b)
    # Cloud & data
    r"\baws\b": 8,
    r"\bpython\b": 8,
    r"\betl\b": 8,
    r"\bairflow\b": 5,
    r"\bmongo\b": 5,
    # Management
    r"\bmanagement d.[eé]quipe\b": 10,
    r"\bpmo\b": 8,
    # Gestion de projet / méthodes (élargi 04/07/2026 : le dico initial, télécom-centré,
    # sous-scorait les postes CDP/PMO/PM visés — 0/73 offres marché ouvert ≥ 65)
    r"\bgestion de projets?\b": 10,
    r"\bpilotage\b": 8,
    r"\bagile\b": 8,
    r"\bscrum\b": 8,
    r"\bjira\b": 5,
    r"\bmoa\b": 8,
    r"\bmoe\b": 5,
    r"\broadmap\b": 5,
    r"\bbacklog\b": 5,
    r"\bbudget\b": 5,
    r"\bplanning\b": 5,
    r"\b(?:prince2|pmp)\b": 5,
    # Data (compléments)
    r"\bsql\b": 8,
    r"\bspark\b": 5,
    r"\bkafka\b": 5,
    r"\b(?:snowflake|dbt)\b": 5,
    r"\b(?:azure|gcp)\b": 5,
    r"\bdocker\b": 5,
    r"\bpower bi\b": 5,
}


def score_hard_skills(text: str) -> float:
    low = text.lower()
    return float(min(100, sum(w for p, w in HARD_SKILLS.items() if re.search(p, low))))
