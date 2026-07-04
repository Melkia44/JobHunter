"""Fixtures partagées des tests Phase 3."""
import pytest

from job_hunter.models import Employer, RawJob


@pytest.fixture
def employers() -> list[Employer]:
    return [
        Employer(name="Manitou Group", tier=1, sector="Industrie", aliases=["Manitou"]),
        Employer(
            name="Groupama (Loire-Bretagne)", tier=1, sector="Assurance",
            aliases=["Groupama", "Gan"],
        ),
        Employer(name="Sopra Steria", tier=2, sector="ESN", aliases=["Sopra"]),
        Employer(name="Orange Business", tier=3, sector="Télécom", aliases=["OBS"]),
    ]


@pytest.fixture
def make_job():
    """Factory de RawJob avec défauts réalistes (format France Travail)."""

    def _make(**overrides) -> RawJob:
        base = dict(
            source="france_travail",
            external_id="1",
            title="Service Delivery Manager H/F",
            company="Acme",
            location="44 - NANTES",
            contract_type="CDI",
            salary_min=None,
            salary_max=None,
            remote_pct=None,
            description=None,
            url="https://example.com/1",
            posted_at=None,
            raw={},
        )
        base.update(overrides)
        return RawJob(**base)

    return _make
