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
    # fuzzy × poids « chef de projet » (75) : proche mais sous un match exact
    score = score_title_match("Cheffe de projets IT", TARGETS)
    assert 60 <= score < 75


def test_priorite_des_postes():
    """SDM > PM > Data Engineer > Chef de projet IT > Chef de projet sans précision."""
    sdm = score_title_match("Service Delivery Manager H/F", TARGETS)
    pm = score_title_match("Product Manager H/F", TARGETS)
    de = score_title_match("Data Engineer F/H", TARGETS)
    cdp_it = score_title_match("Chef de projet informatique H/F", TARGETS)
    cdp = score_title_match("Chef de projet H/F", TARGETS)
    assert sdm > pm > de > cdp_it > cdp
    assert (sdm, pm, de, cdp_it, cdp) == (100, 90, 85, 75, 60)


def test_meilleur_poids_retenu():
    # contient « chef de projet » (75) ET « chef de projet delivery » (90) → 90
    assert score_title_match("Chef de Projet Delivery H/F", TARGETS) == 90


def test_far_title_scores_low():
    assert score_title_match("Comptable fournisseurs", TARGETS) < 60


def test_load_falls_back_when_missing(tmp_path):
    titles = load_target_titles(tmp_path / "absent.yaml")
    assert "chef de projet" in titles
