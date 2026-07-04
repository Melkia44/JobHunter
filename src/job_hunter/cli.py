"""Point d'entrée CLI. Phase 2 en cours : collecte + affichage. Dédup/scoring : Phase 3, Sheet : Phase 4."""
import json
import sys

import typer
from loguru import logger

from job_hunter.collectors import base
from job_hunter.config import get_settings
from job_hunter.models import RawJob

app = typer.Typer(add_completion=False, help="Veille emploi quotidienne.")


def _setup_logging(verbose: bool) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG" if verbose else "INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}",
    )


def _use_os_trust_store() -> None:
    """Vérif TLS via le magasin de certificats de l'OS (proxy/CA d'entreprise).

    Doit précéder toute connexion HTTPS. No-op silencieux si truststore absent
    ou backend SSL non supporté.
    """
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001 — dégradation gracieuse, jamais bloquant
        pass


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Logs DEBUG"),
) -> None:
    _setup_logging(verbose)
    _use_os_trust_store()


@app.command()
def setup() -> None:
    """Affiche la config lue, vérifie les prérequis. (DB + auth SA : phases 3-4)"""
    s = get_settings()
    logger.info(f"spreadsheet_id           = {s.spreadsheet_id or '(non défini)'}")
    logger.info(
        f"france_travail_client_id = {'***' if s.france_travail_client_id else '(non défini)'}"
    )
    logger.info(
        f"france_travail_secret    = {'***' if s.france_travail_client_secret else '(non défini)'}"
    )
    logger.info(f"min_score                = {s.min_score}")
    logger.info(
        f"db_path                  = {s.db_path} "
        f"({'présente' if s.db_path.exists() else 'absente'})"
    )
    sa = s.service_account_path
    logger.info(
        f"service_account          = {sa} ({'présent' if sa.exists() else 'absent'})"
    )
    logger.warning("Création DB + validation auth SA : not implemented yet (phases 3-4)")


@app.command()
def run(
    sources: list[str] | None = typer.Option(
        None,
        "--sources",
        help="jobspy | france_travail | apec | careers_sites (défaut : toutes)",
    ),
    include_linkedin: bool = typer.Option(
        False, "--include-linkedin", help="LinkedIn via JobSpy — local uniquement"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Collecte + dédup + score, sans écriture Sheet"
    ),
    min_score: int | None = typer.Option(
        None, "--min-score", help="Override du seuil (défaut : config, 65)"
    ),
) -> None:
    """Run complet : collecte → dédup → scoring → Sheet → rapport."""
    selected = list(sources) if sources else list(base.SOURCES)
    for src in selected:
        if src not in base.SOURCES:
            logger.error(f"source inconnue : {src} (attendu : {', '.join(base.SOURCES)})")
            raise typer.Exit(code=1)

    all_jobs: list[RawJob] = []
    for src in selected:
        try:
            if src == "jobspy":
                from job_hunter.collectors import jobspy_collector  # import paresseux (pandas)

                all_jobs.extend(jobspy_collector.collect(include_linkedin=include_linkedin))
            elif src == "france_travail":
                from job_hunter.collectors import france_travail_collector

                all_jobs.extend(france_travail_collector.collect(get_settings()))
            elif src == "apec":
                from job_hunter.collectors import apec_rss_collector

                all_jobs.extend(apec_rss_collector.collect(get_settings()))
            elif src == "careers_sites":
                from job_hunter.collectors import careers_sites_collector

                all_jobs.extend(careers_sites_collector.collect(get_settings()))
        except Exception as exc:  # noqa: BLE001 — une source qui casse ne bloque pas les autres
            logger.error(f"{src} : collecte échouée — {exc}")

    _print_samples(all_jobs)
    logger.warning("Dédup/scoring : Phase 3 · écriture Sheet : Phase 4 — run ≈ --dry-run pour l'instant")


def _print_samples(jobs: list[RawJob], n: int = 5) -> None:
    """Volumétrie par source + n exemples de RawJob (description tronquée, raw exclu)."""
    from collections import Counter

    counts = Counter(j.source for j in jobs)
    detail = ", ".join(f"{s}={c}" for s, c in sorted(counts.items())) or "aucune"
    logger.info(f"{len(jobs)} offres collectées ({detail})")

    for job in jobs[:n]:
        data = job.model_dump(mode="json", exclude={"raw"})
        if data.get("description"):
            data["description"] = data["description"][:150] + "…"
        print(json.dumps(data, indent=2, ensure_ascii=False))
    if len(jobs) > n:
        logger.info(f"… et {len(jobs) - n} autres non affichées")
