from job_hunter.scoring.location import score_location


def test_full_remote_wins():
    assert score_location("Paris", 100, "jobspy_indeed") == 100


def test_nort_sur_erdre_top():
    assert score_location("Nort-sur-Erdre", None, "france_travail") == 100


def test_nantes_metro_ft_format():
    assert score_location("44 - NANTES", None, "france_travail") == 90
    assert score_location("44 - ORVAULT", None, "france_travail") == 90


def test_50km_list():
    assert score_location("Ancenis, France", None, "careers_site") == 75


def test_dept44_without_known_city():
    assert score_location("44 - SAVENAY", None, "france_travail") == 60


def test_prefiltered_unknown_city_gets_floor():
    # Cas réels Phase 2 : Rocheservière (85) est DANS le rayon FT 50 km,
    # Laillé (35) est un site Manitou — en zone par construction, jamais 0
    assert score_location("85 - Rocheservière", None, "france_travail") == 75
    assert score_location("Laillé, France", None, "careers_site") == 75


def test_jobspy_unknown_is_neutral():
    assert score_location("Lyon", None, "jobspy_indeed") == 50


def test_explicit_out_of_zone_cp_penalized():
    # Lieu extrait d'une page détail (Cegid) : CP hors 44 = positivement hors zone
    assert score_location("33000", None, "careers_site") == 30
    assert score_location("44300", None, "careers_site") == 60  # CP 44 reste dept-44


def test_partial_remote_beats_unknown():
    assert score_location("Paris", 50, "jobspy_indeed") == 70


def test_empty_location_apec():
    assert score_location("", None, "apec_rss") == 75
