from pathlib import Path

from job_hunter.scoring.tier import score_tier


def test_alias_match(employers):
    assert score_tier("MANITOU", employers) == (100.0, 1)


def test_full_name_match(employers):
    assert score_tier("Manitou Group", employers) == (100.0, 1)


def test_word_boundary_prevents_false_positive(employers):
    # "gan" (alias Groupama) ne doit pas matcher "Morgan Stanley"
    assert score_tier("Morgan Stanley", employers) == (50.0, None)


def test_tier2(employers):
    assert score_tier("Sopra Steria", employers) == (80.0, 2)


def test_out_of_list_is_neutral(employers):
    assert score_tier("Boulangerie Dupont", employers) == (50.0, None)


def test_empty_company(employers):
    assert score_tier("", employers) == (50.0, None)


def test_real_employers_yaml_parses():
    from job_hunter.collectors.careers_sites_collector import load_employers

    path = Path(__file__).parents[1] / "data" / "employers.yaml"
    employers = load_employers(path)
    assert len(employers) >= 65  # 20 cibles historiques + élargissement TOP50 (05/07/2026)
    assert sum(1 for e in employers if e.tier == 1) == 8  # le tier 1 reste les 8 cibles P1
