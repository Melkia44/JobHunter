"""Point d'entrée CLI. Pipeline : collecte → dédup → enrichissement → scoring → rapport.
(Écriture Sheet : Phase 4 — le marquage 'vu' des nouveautés passera alors APRÈS
l'écriture réussie, pour ne jamais perdre d'offre sur un crash.)"""
import json
import sys
from datetime import date

import typer
from loguru import logger

from job_hunter.collectors import base
from job_hunter.config import get_settings
from job_hunter.models import RawJob, ScoredJob

app = typer.Typer(add_completion=False, help="Veille emploi quotidienne.")

_VERBOSE = False


def _setup_logging(verbose: bool) -> None:
    # Windows : stdout est en cp1252 par défaut → les print() du rapport (·, →, …)
    # lèveraient UnicodeEncodeError. On force UTF-8 (no-op si déjà UTF-8 / non supporté).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (ValueError, OSError):  # flux déjà détaché ou non reconfigurable
                pass
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
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Logs DEBUG + dump RawJob bruts"),
) -> None:
    global _VERBOSE
    _VERBOSE = verbose
    _setup_logging(verbose)
    _use_os_trust_store()


@app.command()
def setup() -> None:
    """Affiche la config lue, initialise la DB dédup. (Validation auth SA : Phase 4)"""
    from job_hunter.dedup import SeenJobsDB

    s = get_settings()
    logger.info(f"spreadsheet_id           = {s.spreadsheet_id or '(non défini)'}")
    logger.info(
        f"france_travail_client_id = {'***' if s.france_travail_client_id else '(non défini)'}"
    )
    logger.info(
        f"france_travail_secret    = {'***' if s.france_travail_client_secret else '(non défini)'}"
    )
    logger.info(f"min_score                = {s.min_score} (tier-1 : {s.min_score_tier1})")
    sa = s.service_account_path
    logger.info(
        f"service_account          = {sa} ({'présent' if sa.exists() else 'absent'})"
    )
    SeenJobsDB(s.db_path).close()
    logger.info(f"DB dédup prête           = {s.db_path}")
    logger.warning("Validation auth SA : not implemented yet (Phase 4)")


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
        False, "--dry-run", help="Pipeline complet sans écriture (ni DB dédup, ni Sheet)"
    ),
    min_score: int | None = typer.Option(
        None, "--min-score", help="Override du seuil (défaut : config, 65)"
    ),
) -> None:
    """Run complet : collecte → dédup → scoring → rapport. (Sheet : Phase 4)"""
    from job_hunter.collectors.careers_sites_collector import enrich_descriptions, load_employers
    from job_hunter.dedup import SeenJobsDB
    from job_hunter.normalizer import compute_fingerprint
    from job_hunter.scoring import aggregator
    from job_hunter.scoring.title_match import load_target_titles

    s = get_settings()
    threshold = min_score if min_score is not None else s.min_score

    selected = list(sources) if sources else list(base.SOURCES)
    for src in selected:
        if src not in base.SOURCES:
            logger.error(f"source inconnue : {src} (attendu : {', '.join(base.SOURCES)})")
            raise typer.Exit(code=1)

    # --- Collecte (sources isolées : une qui casse ne bloque pas les autres) ---
    all_jobs: list[RawJob] = []
    for src in selected:
        try:
            if src == "jobspy":
                from job_hunter.collectors import jobspy_collector  # import paresseux (pandas)

                all_jobs.extend(jobspy_collector.collect(include_linkedin=include_linkedin))
            elif src == "france_travail":
                from job_hunter.collectors import france_travail_collector

                all_jobs.extend(france_travail_collector.collect(s))
            elif src == "apec":
                from job_hunter.collectors import apec_rss_collector

                all_jobs.extend(apec_rss_collector.collect(s))
            elif src == "careers_sites":
                from job_hunter.collectors import careers_sites_collector

                all_jobs.extend(careers_sites_collector.collect(s))
        except Exception as exc:  # noqa: BLE001 — une source qui casse ne bloque pas les autres
            logger.error(f"{src} : collecte échouée — {exc}")

    # --- Dédup (dry-run : lecture seule, on ne « brûle » pas les nouveautés) ---
    db = SeenJobsDB(s.db_path)
    today = date.today()
    new_jobs: list[RawJob] = []
    dups = 0
    for job in all_jobs:
        fp = compute_fingerprint(job.company, job.title)
        if db.is_new(fp):
            new_jobs.append(job)
        else:
            dups += 1
        if not dry_run:
            db.mark_seen(fp, job, today)
    db.close()

    if _VERBOSE:
        _dump_raw_samples(new_jobs)

    # --- Enrichissement : description des offres careers nouvelles ---
    to_enrich = [j for j in new_jobs if j.source == "careers_site" and not j.description]
    if to_enrich:
        enrich_descriptions(to_enrich)

    # --- Scoring ---
    employers = load_employers(s.employers_yaml)
    targets = load_target_titles(s.target_titles_yaml)
    new_scored = [aggregator.score_job(j, employers, targets) for j in new_jobs]
    retained = [
        sj for sj in new_scored if aggregator.passes_threshold(sj, threshold, s.min_score_tier1)
    ]
    retained.sort(key=lambda sj: sj.score, reverse=True)

    _report(len(all_jobs), dups, new_scored, retained, threshold, s.min_score_tier1, dry_run)


def _report(
    total: int,
    dups: int,
    new_scored: list[ScoredJob],
    retained: list[ScoredJob],
    threshold: int,
    threshold_t1: int,
    dry_run: bool,
) -> None:
    logger.info(
        f"{total} collectées · {dups} déjà vues · {len(new_scored)} nouvelles · "
        f"{len(retained)} retenues (seuil {threshold}, tier-1 {threshold_t1})"
    )
    for sj in retained[:5]:
        b = sj.breakdown
        print(
            f"\n[{sj.score:5.1f}] {sj.job.title} — {sj.job.company} ({sj.job.location or 'lieu n.c.'})\n"
            f"        hard {b.hard_skills:.0f} · titre {b.title_match:.0f} · soft {b.soft_skills:.0f}"
            f" · loc {b.location:.0f} · tier {b.tier:.0f} → {sj.match_reason}\n"
            f"        {sj.job.url}"
        )
    if len(retained) > 5:
        logger.info(f"… et {len(retained) - 5} autres retenues")
    if dry_run:
        logger.info("dry-run : DB dédup non modifiée")
    logger.warning("Écriture Sheet : Phase 4")


def _dump_raw_samples(jobs: list[RawJob], n: int = 3) -> None:
    """Mode -v : RawJob bruts (debug mapping collecteurs, ex. calage APEC)."""
    for job in jobs[:n]:
        data = job.model_dump(mode="json")
        if data.get("description"):
            data["description"] = data["description"][:150] + "…"
        print(json.dumps(data, indent=2, ensure_ascii=False))
