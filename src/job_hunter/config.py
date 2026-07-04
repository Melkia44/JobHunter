"""Configuration : os.environ d'abord, fallback .env (comportement pydantic-settings par défaut).

Tous les chemins sont ancrés à la racine du repo, pas au répertoire courant :
`uv run job-hunter` lancé d'ailleurs raterait sinon le .env et créerait data/ au
mauvais endroit. (Valide car uv sync installe le projet en editable → __file__
pointe dans src/, pas dans site-packages.)
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]  # src/job_hunter/config.py → racine


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # utf-8-sig : tolère le BOM que PowerShell/Notepad ajoutent souvent sous Windows
        env_file=_REPO_ROOT / ".env", env_file_encoding="utf-8-sig", extra="ignore"
    )

    # Secrets — défaut "" : validés au point d'usage, pas au chargement,
    # pour que `--help` et `setup` fonctionnent sans .env
    france_travail_client_id: str = ""
    france_travail_client_secret: str = ""
    spreadsheet_id: str = ""

    # Scoring
    min_score: int = 65
    min_score_tier1: int = 50  # seuil abaissé employeurs tier-1 (acté 04/07/2026)

    # Chemins (ancrés à la racine du repo)
    db_path: Path = _REPO_ROOT / "data" / "seen_jobs.db"
    service_account_path: Path = _REPO_ROOT / "secrets" / "service_account.json"
    employers_yaml: Path = _REPO_ROOT / "data" / "employers.yaml"
    target_titles_yaml: Path = _REPO_ROOT / "data" / "target_titles.yaml"
    apec_feeds_yaml: Path = _REPO_ROOT / "data" / "apec_feeds.yaml"


def get_settings() -> Settings:
    return Settings()
