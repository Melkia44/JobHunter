"""Profil de recherche : tout ce qui est propre à la personne (métiers, compétences,
zone, requêtes, sources) vit dans data/profile.yaml — le code n'en contient plus.

Chargé une fois par process (lru_cache). Chemin surchargeable via PROFILE_YAML.
"""
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from job_hunter.normalizer import normalize


class TargetTitle(BaseModel):
    title: str
    weight: float = 100
    # Intitulé ambigu (« chef de projet », « service manager »…) : sans marqueur IT dans
    # l'intitulé, plafonné à generic_cap. N'a d'effet que si filters.it_only est vrai.
    gated: bool = False


class Center(BaseModel):
    city: str
    insee: str  # code commune INSEE (France Travail)
    lat: float
    lon: float


class Location(BaseModel):
    center: Center
    radius_km: int = 50
    home: str = ""                                   # score 100
    core_cities: list[str] = Field(default_factory=list)   # score 90
    area_cities: list[str] = Field(default_factory=list)   # score 75
    departments: list[str] = Field(default_factory=list)   # score 60 ; CP hors liste → 30
    department_names: list[str] = Field(default_factory=list)
    # Départements acceptés par les filtres client (APEC, Hellowork) : ~ le rayon autour du centre
    search_departments: list[str] = Field(default_factory=list)

    def zone_cities(self) -> list[str]:
        """Villes de la zone, normalisées « tout-tirets » (saint herblain → saint-herblain)."""
        cities = [self.home, self.center.city, *self.core_cities, *self.area_cities]
        return [normalize(c).replace(" ", "-") for c in cities if c]


class Search(BaseModel):
    jobspy_queries: list[str] = Field(default_factory=list)
    apec_keywords: list[str] = Field(default_factory=list)
    france_travail_rome: list[str] = Field(default_factory=list)


class Filters(BaseModel):
    # Écarte les métiers hors IT (BTP, industrie, marketing…) et active le plafond des
    # intitulés gated. À passer à false pour un profil non IT.
    it_only: bool = True


class Profile(BaseModel):
    sources: list[str]
    titles: list[TargetTitle]
    generic_cap: float = 60
    hard_skills: dict[str, int] = Field(default_factory=dict)
    soft_skills: dict[str, int] = Field(default_factory=dict)
    location: Location
    search: Search = Field(default_factory=Search)
    filters: Filters = Field(default_factory=Filters)

    @field_validator("titles", mode="before")
    @classmethod
    def _plain_titles(cls, v: list) -> list:
        # Raccourci YAML : « - "product manager" » = poids 100, non gated
        return [{"title": t} if isinstance(t, str) else t for t in v or []]

    def target_names(self) -> list[str]:
        return [normalize(t.title) for t in self.titles]

    def title_rule(self, target_norm: str) -> TargetTitle | None:
        return next((t for t in self.titles if normalize(t.title) == target_norm), None)


def default_profile_path() -> Path:
    from job_hunter.config import get_settings  # import local : config importe peu

    return get_settings().profile_yaml


@lru_cache(maxsize=4)
def get_profile(path: Path | None = None) -> Profile:
    p = path or default_profile_path()
    if not p.exists():
        raise RuntimeError(f"Profil introuvable : {p} (copier/adapter data/profile.yaml)")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    profile = Profile.model_validate(data)
    from job_hunter.collectors.base import SOURCES

    unknown = [s for s in profile.sources if s not in SOURCES]
    if unknown:
        raise RuntimeError(f"profile.yaml : source(s) inconnue(s) {unknown} (attendu : {SOURCES})")
    return profile
