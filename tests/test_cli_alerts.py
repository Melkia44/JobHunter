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


def test_panne_apec_non_bloquante():
    """APEC 403 depuis GitHub Actions : signalée, mais ne fait pas échouer le run."""
    from job_hunter.cli import _blocking_alerts

    alerts = [
        "apec : 0 offre collectée — source probablement en panne",
        "email_alerts : collecte échouée — login",
        "Sheet non mis à jour — 403",
    ]
    assert _blocking_alerts(alerts) == alerts[1:]
    assert _blocking_alerts(alerts[:1]) == []
