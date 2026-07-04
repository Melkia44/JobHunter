"""Convention des collecteurs : un module = une fonction collect(...) -> list[RawJob].

Pas de Protocol ni de classe de base : les signatures varient (flags, chemins de
config) et le dispatch est explicite dans cli.py — une abstraction n'apporterait rien.
"""
from collections.abc import Callable

from job_hunter.models import RawJob

CollectFn = Callable[..., list[RawJob]]

# Noms de sources acceptés par `run --sources` (ordre = ordre d'exécution)
SOURCES = ("jobspy", "france_travail", "apec", "careers_sites")
