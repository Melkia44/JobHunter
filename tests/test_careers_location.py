"""Lieu des pages détail careers (Cegid : Arkéa, Docaposte) — incident du 23/09/2026 :
7/7 offres Arkéa de Brest/Rennes affichées « Nantes » (mot trouvé dans le menu)."""
from job_hunter.collectors.careers_sites_collector import _page_text_and_location, _zone_tagged
from job_hunter.scoring.location import score_location

_MENU = '<nav><select><option>Brest</option><option>Nantes</option></select></nav>'


def _page(lieu_block: str) -> str:
    return (f"<html><body>{_MENU}<main><h1>Responsable d'application/Projet H/F</h1>"
            f"<h3>Contrat</h3><p>CDI</p>{lieu_block}<h3>Date</h3><p>22/09/2026</p>"
            "<p>Poste basé à Brest, déplacements ponctuels.</p></main></body></html>")


def test_lieu_lu_dans_le_champ_pas_dans_le_menu():
    _, lieu = _page_text_and_location(_page("<h3>Lieu</h3><p>Le Relecq-Kerhuon</p>"))
    assert lieu == "Le Relecq-Kerhuon"


def test_lieu_hors_zone_tague_et_note_30():
    loc = _zone_tagged("Le Relecq-Kerhuon")
    assert loc == "Le Relecq-Kerhuon (hors zone)"
    assert score_location(loc, None, "careers_site") == 30.0
    assert score_location(_zone_tagged("St Grégoire"), None, "careers_site") == 30.0


def test_lieu_en_zone_conserve():
    assert _zone_tagged("Nantes") == "Nantes"
    assert _zone_tagged("Saint-Herblain") == "Saint-Herblain"
    assert _zone_tagged("Loire-Atlantique") == "Loire-Atlantique"
    assert _zone_tagged("Sainte-Luce-sur-Loire") == "Sainte-Luce-sur-Loire"


def test_sans_champ_lieu_rien_de_devine():
    _, lieu = _page_text_and_location(_page(""))
    assert lieu == ""
