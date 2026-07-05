"""Point d'entrée CLI. Pipeline : collecte → dédup → enrichissement → scoring → Sheet → rapport.

Ordre de marquage (acté) : les offres NOUVELLES ne sont marquées 'vues' qu'après
écriture Sheet réussie — un crash d'écriture ne perd jamais d'offre. Le last_seen
des offres déjà connues est mis à jour sans condition (inoffensif).
"""
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
    """Affiche la config, initialise la DB dédup, valide l'auth Google Sheets."""
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
    try:
        from job_hunter.sheet_writer import SheetWriter  # import paresseux (googleapiclient)

        title = SheetWriter(s).check_access()
        logger.info(f"Auth Google Sheets OK — classeur : « {title} »")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Auth Google Sheets NON validée : {exc}")
        logger.warning("Rappel : le Sheet doit être partagé en Éditeur avec l'email du SA (sinon 403)")


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
    """Run complet : collecte → dédup → scoring → Sheet → rapport."""
    from job_hunter.collectors.careers_sites_collector import enrich_descriptions, load_employers
    from job_hunter.dedup import SeenJobsDB
    from job_hunter.normalizer import compute_fingerprint
    from job_hunter.reporter import report
    from job_hunter.scoring import aggregator
    from job_hunter.scoring.tier import find_employer
    from job_hunter.scoring.title_match import load_target_titles

    s = get_settings()
    threshold = min_score if min_score is not None else s.min_score
    alerts: list[str] = []

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
            alerts.append(f"{src} : collecte échouée — {exc}")

    # --- Filtre contrat : CDI uniquement (écarte CDD, stage, alternance, intérim…) ---
    kept = [j for j in all_jobs if not base.is_excluded_contract(j)]
    if len(all_jobs) - len(kept):
        logger.info(f"filtre contrat : {len(all_jobs) - len(kept)} offre(s) non-CDI écartée(s)")
    all_jobs = kept

    # --- Dédup (les nouveautés ne sont PAS encore marquées : cf. docstring) ---
    db = SeenJobsDB(s.db_path)
    today = date.today()
    new_jobs: list[tuple[str, RawJob]] = []  # (fingerprint, job)
    dups = 0
    for job in all_jobs:
        fp = compute_fingerprint(job.company, job.title)
        if db.is_new(fp):
            new_jobs.append((fp, job))
        else:
            dups += 1
            if not dry_run:
                db.mark_seen(fp, job, today)  # last_seen only, inoffensif

    if _VERBOSE:
        _dump_raw_samples([j for _, j in new_jobs])

    # --- Enrichissement : description des offres careers nouvelles ---
    to_enrich = [j for _, j in new_jobs if j.source == "careers_site" and not j.description]
    if to_enrich:
        enrich_descriptions(to_enrich)

    # --- Scoring ---
    employers = load_employers(s.employers_yaml)
    targets = load_target_titles(s.target_titles_yaml)
    new_scored = [aggregator.score_job(j, employers, targets) for _, j in new_jobs]
    retained = [
        sj for sj in new_scored if aggregator.passes_threshold(sj, threshold, s.min_score_tier1)
    ]
    retained.sort(key=lambda sj: sj.score, reverse=True)

    # --- Écriture Sheet puis marquage des nouveautés ---
    appended: int | None = None
    if not dry_run:
        wrote_ok = False
        try:
            from job_hunter.sheet_writer import SheetWriter  # import paresseux

            writer = SheetWriter(s)
            appended = writer.append_offers(retained, today)
            touched = {
                emp.name
                for sj in retained
                if sj.matched_employer_tier
                and (emp := find_employer(sj.job.company, employers)) is not None
            }
            writer.update_employer_statuses(touched, employers, today)
            writer.recompute_pilotage()
            writer.update_sources_status(_source_stats(all_jobs, alerts, selected), today.strftime("%d/%m/%Y"))
            wrote_ok = True
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Écriture Sheet échouée — {exc}")
            alerts.append(f"Sheet non mis à jour — {exc} (nouveautés NON marquées, re-tentées demain)")
        if wrote_ok:
            for fp, job in new_jobs:
                db.mark_seen(fp, job, today)
    db.close()

    report(len(all_jobs), dups, len(new_scored), retained, threshold, s.min_score_tier1, appended, dry_run, alerts)


def _source_stats(
    all_jobs: list[RawJob], alerts: list[str], selected: list[str]
) -> dict[str, tuple[int, bool]]:
    """Par source logique checkée ce run : (nb d'offres collectées, ok). ok=False si la
    collecte a levé (l'erreur est dans alerts, préfixée « {src} : »)."""
    from collections import Counter

    logical = {"france_travail": "france_travail", "apec_rss": "apec", "careers_site": "careers_sites"}
    counts: Counter = Counter()
    for job in all_jobs:
        key = "jobspy" if job.source.startswith("jobspy") else logical.get(job.source, job.source)
        counts[key] += 1
    errored = {src for src in selected if any(a.startswith(f"{src} :") for a in alerts)}
    return {src: (counts.get(src, 0), src not in errored) for src in selected}


def _dump_raw_samples(jobs: list[RawJob], n: int = 3) -> None:
    """Mode -v : RawJob bruts (debug mapping collecteurs, ex. calage APEC)."""
    for job in jobs[:n]:
        data = job.model_dump(mode="json")
        if data.get("description"):
            data["description"] = data["description"][:150] + "…"
        print(json.dumps(data, indent=2, ensure_ascii=False))
