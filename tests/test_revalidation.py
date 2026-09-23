"""Revalidation des lignes « Nouvelle » de l'onglet Offres (23/09/2026)."""
from job_hunter.sheet_writer import revalidate_rows


def _row(emp, title, statut="Nouvelle", lieu="Nantes", contrat="n.c.", url=""):
    # A date, B employeur, C intitulé, D lieu, E contrat, F salaire, G publication,
    # H lien, I source, J match, K statut
    return ["23/09/2026", emp, title, lieu, contrat, "n.c.", "n.c.", url, "Indeed", "", statut]


def test_doublon_garde_la_premiere_occurrence_active():
    rows = [
        _row("Groupe SYD", "Chef de Projet Technique H/F", url="u1"),
        _row("SYD GROUPE", "Chef de Projet Technique H/F", url="u2"),
        _row("Autre", "Data Engineer H/F", url="u3"),
        _row("Autre bis", "Data Engineer H/F", url="u3"),  # même lien
    ]
    assert revalidate_rows(rows) == [(1, "Doublon de la ligne 2"), (3, "Doublon de la ligne 4")]


def test_une_ligne_archivee_ne_sert_pas_de_reference():
    """Sinon les deux exemplaires finissent archivés (cas Consort lignes 96/97)."""
    rows = [
        _row("CONSORT Group", "Product Owner Senior H/F Consortia - Testing", statut="Archivée"),
        _row("CONSORT Group", "Product Owner Senior H/F"),
    ]
    assert revalidate_rows(rows) == []


def test_une_ligne_suivie_a_la_main_sert_de_reference_et_n_est_jamais_touchee():
    rows = [
        _row("Groupe SII", "Chef de Projet Delivery (F/H)", statut="Candidature envoyée"),
        _row("Groupe SII (P2)", "Chef de Projet Delivery (F/H) – Nantes"),
        _row("Lehibou", "PO (IT) / Freelance", statut="Entretien"),
    ]
    assert revalidate_rows(rows) == [(1, "Doublon de la ligne 2")]


def test_contrat_metier_lieu():
    rows = [
        _row("lehibou", "Product Owner Digital Workplace - Nantes (44) (IT) / Freelance"),
        _row("Ministère", "Chef de projet maîtrise d'oeuvre - RFDE (F/H)"),
        _row("Arkéa", "Business analyst H/F", lieu="Le Relecq-Kerhuon (hors zone)"),
        _row("Capgemini", "Tech Lead H/F", contrat="CDD"),
        _row("CGI", "Consultant(e) Data Engineer F/H"),
    ]
    assert revalidate_rows(rows) == [
        (0, "Contrat hors CDI"), (1, "Métier hors IT"), (2, "Lieu hors zone"), (3, "Contrat hors CDI"),
    ]
