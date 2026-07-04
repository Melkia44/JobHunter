"""Agrégation des 5 sous-scores (35/20/15/15/15) + seuils + match_reason."""
from job_hunter.models import Employer, RawJob, ScoreBreakdown, ScoredJob
from job_hunter.normalizer import compute_fingerprint
from job_hunter.scoring.hard_skills import score_hard_skills
from job_hunter.scoring.location import score_location
from job_hunter.scoring.soft_skills import score_soft_skills
from job_hunter.scoring.tier import score_tier
from job_hunter.scoring.title_match import score_title_match

# Recalibré le 04/07/2026 sur 73 offres réelles : les poids du brief (hard .35)
# rendaient 65 inatteignable hors tier-1 (plafond ~48 pour un titre parfait à Nantes).
WEIGHTS: dict[str, float] = {
    "hard_skills": 0.25,
    "title_match": 0.30,
    "soft_skills": 0.10,
    "location": 0.20,
    "tier": 0.15,
}


def score_job(job: RawJob, employers: list[Employer], target_titles: list[str]) -> ScoredJob:
    # Titre TOUJOURS inclus : un titre « SDM » ne doit pas perdre son 'delivery'
    # sous prétexte qu'une description existe.
    text = f"{job.title}\n{job.description or ''}"
    tier_score, tier_matched = score_tier(job.company, employers)
    breakdown = ScoreBreakdown(
        hard_skills=score_hard_skills(text),
        title_match=score_title_match(job.title, target_titles),
        soft_skills=score_soft_skills(text),
        location=score_location(job.location, job.remote_pct, job.source),
        tier=tier_score,
    )
    score = round(sum(w * getattr(breakdown, k) for k, w in WEIGHTS.items()), 1)
    scored = ScoredJob(
        job=job,
        fingerprint=compute_fingerprint(job.company, job.title),
        score=score,
        breakdown=breakdown,
        matched_employer_tier=tier_matched,
        match_reason="",
    )
    scored.match_reason = build_match_reason(scored)
    return scored


def passes_threshold(scored: ScoredJob, min_score: int, min_score_tier1: int) -> bool:
    """Seuil abaissé pour les tier-1 (souvent sans description → sous-scorées)."""
    if scored.matched_employer_tier == 1:
        return scored.score >= min_score_tier1
    return scored.score >= min_score


def build_match_reason(scored: ScoredJob) -> str:
    parts: list[str] = []
    if scored.matched_employer_tier:
        parts.append(f"P{scored.matched_employer_tier} cible")
    if scored.breakdown.location >= 90:
        parts.append(scored.job.location.split(",")[0].strip())
    if scored.breakdown.title_match == 100:
        parts.append("titre exact")
    elif scored.breakdown.hard_skills >= 50:
        parts.append("skills forts")
    return ", ".join(parts) if parts else "hors-liste à valider"
