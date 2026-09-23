"""Alertes du run (sources en panne silencieuse)."""


def test_source_a_zero_offre_sans_erreur_signalee():
    """Incident du 23/09/2026 : collecte mail à 0, run vert, rien ne le signalait."""
    from job_hunter.cli import _silent_zero_sources
    from job_hunter.models import RawJob

    job = RawJob.model_construct(source="france_travail")
    alerts = ["apec : collecte échouée — timeout"]
    selected = ["france_travail", "apec", "email_alerts"]
    # apec est déjà en erreur (alerte existante) : pas de double signalement
    assert _silent_zero_sources([job], alerts, selected) == ["email_alerts"]
