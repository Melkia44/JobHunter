from job_hunter.normalizer import normalize
from job_hunter.scoring.title_match import (
    DEFAULT_TARGET_TITLES,
    load_target_titles,
    score_title_match,
)

TARGETS = [normalize(t) for t in DEFAULT_TARGET_TITLES]


def test_exact_substring():
    assert score_title_match("Service Delivery Manager H/F", TARGETS) == 100


def test_manitou_real_title():
    # Cas réel Phase 2 : "des" casse le substring du brief, couvert par "operations services"
    assert score_title_match("Responsable des Opérations Services", TARGETS) == 100


def test_fuzzy_close_title():
    score = score_title_match("Cheffe de projets IT", TARGETS)
    assert 70 <= score < 100


def test_far_title_scores_low():
    assert score_title_match("Comptable fournisseurs", TARGETS) < 60


def test_load_falls_back_when_missing(tmp_path):
    titles = load_target_titles(tmp_path / "absent.yaml")
    assert "chef de projet" in titles
