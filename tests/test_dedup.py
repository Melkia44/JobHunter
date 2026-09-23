from datetime import date

from job_hunter.dedup import SeenJobsDB
from job_hunter.normalizer import compute_fingerprint


def test_new_then_seen(tmp_path, make_job):
    db = SeenJobsDB(tmp_path / "seen.db")
    job = make_job()
    fp = compute_fingerprint(job.company, job.title)
    assert db.is_new(fp)
    db.mark_seen(fp, job, date(2026, 7, 1))
    assert not db.is_new(fp)
    db.close()


def test_repost_same_content_is_not_new(tmp_path, make_job):
    """Repost : id source différent mais même (company, title) → même fingerprint."""
    db = SeenJobsDB(tmp_path / "seen.db")
    original = make_job(external_id="1")
    repost = make_job(external_id="999", title="Service Delivery Manager (H/F)")
    fp1 = compute_fingerprint(original.company, original.title)
    fp2 = compute_fingerprint(repost.company, repost.title)
    db.mark_seen(fp1, original, date(2026, 7, 1))
    assert fp1 == fp2
    assert not db.is_new(fp2)
    db.close()


def test_mark_seen_updates_last_seen_without_duplicating(tmp_path, make_job):
    db = SeenJobsDB(tmp_path / "seen.db")
    job = make_job()
    fp = compute_fingerprint(job.company, job.title)
    db.mark_seen(fp, job, date(2026, 7, 1))
    db.mark_seen(fp, job, date(2026, 7, 3))
    row = db._conn.execute(
        "SELECT COUNT(*), MIN(first_seen), MAX(last_seen) FROM seen_jobs"
    ).fetchone()
    assert row == (1, "2026-07-01", "2026-07-03")
    db.close()


def test_doublons_sheet_du_23_09_meme_empreinte():
    """Paires vues dans la Sheet le 23/09/2026 : mêmes offres, libellés différents."""
    from job_hunter.normalizer import compute_fingerprint as fp

    assert fp("Groupe SYD", "Chef de Projet Technique H/F") == fp("SYD GROUPE", "Chef de Projet Technique H/F")
    assert fp("CONSORT Group", "Product Owner Senior H/F") == fp(
        "CONSORT Group", "Product Owner Senior H/F Consortia - Testing, Développement, Data & IA · Nantes"
    )
    assert fp("Groupe SII (P2)", "Chef de Projet Delivery (F/H) – Nantes") == fp(
        "Groupe SII", "Chef de Projet Delivery (F/H)"
    )
    assert fp("MANITOU GROUP (P1)", "Chef de Projets Senior (F/H)") == fp("MANITOU", "Chef de Projets Senior F/H")
    # Offres réellement différentes : empreintes différentes
    assert fp("CONSORT Group", "Product Owner Senior H/F") != fp("CONSORT Group", "Product Owner H/F")
    assert fp("Groupe SYD", "Chef de Projet Technique H/F") != fp("Groupe SII", "Chef de Projet Technique H/F")


def test_empreinte_v1_consultee_pour_la_transition(tmp_path, make_job):
    from job_hunter.normalizer import legacy_fingerprint

    db = SeenJobsDB(tmp_path / "seen.db")
    job = make_job()
    db.mark_seen(legacy_fingerprint(job.company, job.title), job, date(2026, 7, 1))
    assert not db.is_new(legacy_fingerprint(job.company, job.title))
    db.close()
