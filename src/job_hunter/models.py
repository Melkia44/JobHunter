"""Contrats de données du pipeline."""
from datetime import date
from typing import Literal

from pydantic import BaseModel

Source = Literal[
    "jobspy_linkedin",
    "jobspy_indeed",
    "jobspy_glassdoor",
    "jobspy_google",
    "france_travail",
    "apec_rss",
    "careers_site",
]


class RawJob(BaseModel):
    """Offre normalisée en sortie de collecteur."""

    source: Source
    external_id: str            # ID stable pour dédup intra-source
    title: str
    company: str
    location: str
    contract_type: str | None   # "CDI", "CDD", "Freelance", "Intérim"
    salary_min: int | None      # k€ annuel brut
    salary_max: int | None
    remote_pct: int | None      # 0, 50, 100
    description: str | None
    url: str                    # apply_url direct si dispo
    posted_at: date | None
    raw: dict                   # payload brut source-dépendant (debug)


class ScoreBreakdown(BaseModel):
    """Sous-scores, chacun sur 0-100."""

    hard_skills: float
    title_match: float
    soft_skills: float
    location: float
    tier: float


class ScoredJob(BaseModel):
    """Offre scorée, prête pour filtrage et écriture Sheet."""

    job: RawJob
    fingerprint: str            # sha256(company_norm + "|" + title_norm)
    score: float                # agrégé pondéré sur 100
    breakdown: ScoreBreakdown
    matched_employer_tier: Literal[1, 2, 3] | None
    match_reason: str           # 1 phrase → colonne "Match (pourquoi)"


class Employer(BaseModel):
    """Employeur cible du référentiel employers.yaml."""

    name: str
    tier: Literal[1, 2, 3]
    sector: str
    careers_url: str | None = None
    aliases: list[str] = []     # variantes de nom pour matching
    ats: str | None = None      # dispatch scraper par ATS (smartrecruiters, teamtailor, ssr, …)
    link_pattern: str | None = None  # pour ats=ssr : motif des hrefs d'offres du listing
