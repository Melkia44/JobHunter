from job_hunter.normalizer import compute_fingerprint, normalize


def test_lowercase_and_accents():
    assert normalize("Ingénieur Réseau") == "ingenieur reseau"


def test_strips_hf_variants():
    assert normalize("Service Delivery Manager H/F") == "service delivery manager"
    assert normalize("Chef de Projet (H/F)") == "chef de projet"
    assert normalize("Data Engineer F/H.") == "data engineer"


def test_collapses_whitespace_and_trailing_punct():
    assert normalize("  PMO   transverse !") == "pmo transverse"


def test_fingerprint_invariant_to_cosmetics():
    assert compute_fingerprint("ACME", "SDM H/F") == compute_fingerprint("Acme", "SDM")


def test_fingerprint_differs_by_company():
    assert compute_fingerprint("Acme", "SDM") != compute_fingerprint("Globex", "SDM")
