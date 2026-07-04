"""Rapport CLI de fin de run : volumétrie, top 3, alertes."""
from loguru import logger

from job_hunter.models import ScoredJob


def report(
    total: int,
    dups: int,
    new_count: int,
    retained: list[ScoredJob],
    threshold: int,
    threshold_t1: int,
    appended: int | None,
    dry_run: bool,
    alerts: list[str],
) -> None:
    logger.info(
        f"{total} collectées · {dups} déjà vues · {new_count} nouvelles · "
        f"{len(retained)} retenues (seuil {threshold}, tier-1 {threshold_t1})"
    )
    print("\nTop 3 du jour :" if retained else "\nAucune offre retenue aujourd'hui.")
    for sj in retained[:3]:
        b = sj.breakdown
        print(
            f"\n[{sj.score:5.1f}] {sj.job.title} — {sj.job.company} ({sj.job.location or 'lieu n.c.'})\n"
            f"        hard {b.hard_skills:.0f} · titre {b.title_match:.0f} · soft {b.soft_skills:.0f}"
            f" · loc {b.location:.0f} · tier {b.tier:.0f} → {sj.match_reason}\n"
            f"        {sj.job.url}"
        )
    if len(retained) > 3:
        logger.info(f"… et {len(retained) - 3} autre(s) retenue(s) — voir le Sheet")
    if dry_run:
        logger.info("dry-run : ni DB dédup ni Sheet modifiés")
    elif appended is not None:
        logger.info(f"Sheet : {appended} offre(s) écrite(s)")
    for alert in alerts:
        logger.warning(f"ALERTE : {alert}")
