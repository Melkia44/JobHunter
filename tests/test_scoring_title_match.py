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


def test_synonymes_sdm_run_itsm():
    assert score_title_match("IT Service Manager H/F", TARGETS) == 95
    assert score_title_match("Service Owner ServiceNow", TARGETS) == 95
    assert score_title_match("Responsable MCO / Run", TARGETS) == 95
    assert score_title_match("Head of Service Delivery", TARGETS) == 100
    assert score_title_match("Responsable de production informatique H/F", TARGETS) == 90


def test_synonymes_ambigus_plafonnes_hors_it():
    """Industrie, service client, juridique : pas un poste SDM sans marqueur IT."""
    assert score_title_match("Responsable de production H/F", TARGETS) == 60
    assert score_title_match("Responsable d'exploitation transport", TARGETS) == 60
    assert score_title_match("Customer Service Manager", TARGETS) == 60
    assert score_title_match("Contract Manager juridique", TARGETS) == 60


def test_fuzzy_hors_it_plafonne():
    """Run #90 : intitulés retail/restauration remontés par le fuzzy (≈ « responsable run »)."""
    for t in ("RESPONSABLE BRASSERIE - H/F", "Responsable de magasin (H/F)", "Manager H/F"):
        assert score_title_match(t, TARGETS) <= 60, t
    # « applicatif » est un marqueur IT : le fuzzy n'est pas plafonné
    assert score_title_match("Responsable applicatif H/F", TARGETS) > 60


def test_project_manager_comme_chef_de_projet():
    """Michael Page « Project Manager H/F » (IT, 50-60 k€) écarté le 23/09 par le plafond fuzzy."""
    assert score_title_match("Project Manager H/F", TARGETS) == 60  # sans marqueur IT : description tranche
    assert score_title_match("IT Project Manager", TARGETS) == 75
    assert score_title_match("Senior Project Manager Data", TARGETS) == 75
