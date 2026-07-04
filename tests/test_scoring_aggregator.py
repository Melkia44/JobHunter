"""Agrégateur + soft skills + seuils (fichier ajouté vs brief, signalé au STOP Phase 3)."""
import pytest

from job_hunter.normalizer import normalize
from job_hunter.scoring import aggregator
from job_hunter.scoring.soft_skills import score_soft_skills
from job_hunter.scoring.title_match import DEFAULT_TARGET_TITLES

TARGETS = [normalize(t) for t in DEFAULT_TARGET_TITLES]


def test_soft_skills_weights():
    # management 20 + international 15 + anglais 15
    assert score_soft_skills("Management international, anglais courant") == 50


def test_score_job_breakdown(make_job, employers):
    job = make_job(
        description="Pilotage delivery, SLA, ITIL, management d'équipe, anglais courant"
    )
    scored = aggregator.score_job(job, employers, TARGETS)
    # titre concaténé : delivery 15 + sla 10 + itil 15 + mgmt équipe 10 + pilotage 8
    assert scored.breakdown.hard_skills == 58
    assert scored.breakdown.title_match == 100
    assert scored.breakdown.soft_skills == 35  # management 20 + anglais 15
    assert scored.breakdown.location == 90
    assert scored.breakdown.tier == 50  # inconnu = neutre
    assert scored.score == pytest.approx(73.5, abs=0.1)
    assert aggregator.passes_threshold(scored, 65, 50)
    assert "titre exact" in scored.match_reason


def test_tier1_floor_as_safety_net(make_job, employers):
    """Filet de sécurité : P1 pertinente mais description pauvre → sous 65, au-dessus de 50."""
    job = make_job(
        title="Responsable des Opérations Services",
        company="Manitou Group",
        location="Ancenis, France",
        source="careers_site",
        description="Encadrement des opérations de service après-vente",
    )
    scored = aggregator.score_job(job, employers, TARGETS)
    assert 50 <= scored.score < 65  # ~61 : hard 0, titre 100, soft 10, loc 75, tier 100
    assert aggregator.passes_threshold(scored, 65, 50)
    assert not aggregator.passes_threshold(scored, 65, 65)
    assert scored.match_reason.startswith("P1 cible")


def test_perfect_title_alone_is_not_enough(make_job, employers):
    """Sans description ni tier, le titre parfait ne passe pas 65 (garde-fou domaine)."""
    job = make_job(company="ESN Quelconque")
    scored = aggregator.score_job(job, employers, TARGETS)
    assert scored.score == pytest.approx(59.25, abs=0.1)
    assert not aggregator.passes_threshold(scored, 65, 50)
