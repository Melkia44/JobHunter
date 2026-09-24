"""Veille implantations : filtrage des établissements Sirene."""
from datetime import date

import pytest

from job_hunter import implantations as imp
from job_hunter.config import get_settings


@pytest.fixture
def cfg():
    return imp.load_config(get_settings().implantations_yaml)


def _etab(siret="12345678900011", tranche="32", cat="GE", naf_etab="62.02A",
          naf_ul="62.02A", etat="A", x="355400", y="6689400"):
    return {
        "siret": siret,
        "siren": siret[:9],
        "dateCreationEtablissement": "2026-09-01",
        "uniteLegale": {
            "denominationUniteLegale": "ACME CONSULTING",
            "trancheEffectifsUniteLegale": tranche,
            "categorieEntrepriseUniteLegale": cat,
            "activitePrincipaleUniteLegale": naf_ul,
        },
        "adresseEtablissement": {
            "codePostalEtablissement": "44000",
            "libelleCommuneEtablissement": "NANTES",
            "coordonneeLambertAbscisseEtablissement": x,
            "coordonneeLambertOrdonneeEtablissement": y,
        },
        "periodesEtablissement": [
            {"dateFin": None, "etatAdministratifEtablissement": etat,
             "activitePrincipaleEtablissement": naf_etab, "enseigne1Etablissement": None},
        ],
    }


def test_query():
    q = imp.build_query("44", date(2026, 7, 1), date(2026, 9, 24))
    assert q == ("codePostalEtablissement:44* AND dateCreationEtablissement:"
                 "[2026-07-01 TO 2026-09-24] AND etablissementSiege:false")


def test_keeps_esn_group(cfg):
    [i] = imp.select([_etab()], cfg)
    assert i.entreprise == "ACME CONSULTING"
    assert i.secteur.startswith("Programmation")
    assert i.effectif_groupe == "250-499"
    assert i.km_nantes == 0
    assert i.url.endswith("12345678900011")


def test_drops_small_company(cfg):
    assert imp.select([_etab(tranche="11", cat="PME")], cfg) == []


def test_unknown_tranche_but_eti_kept(cfg):
    assert len(imp.select([_etab(tranche="NN", cat="ETI")], cfg)) == 1


def test_drops_retail_store(cfg):
    assert imp.select([_etab(naf_etab="47.11F", naf_ul="47.11F")], cfg) == []


def test_naf_matches_on_unite_legale(cfg):
    # établissement codé « commerce » mais groupe télécom → retenu
    [i] = imp.select([_etab(naf_etab="47.42Z", naf_ul="61.10Z")], cfg)
    assert i.naf == "61.10Z"


def test_drops_closed(cfg):
    assert imp.select([_etab(etat="F")], cfg) == []


def test_distance_saint_nazaire(cfg):
    [i] = imp.select([_etab(x="307000", y="6700000")], cfg)
    assert 45 <= i.km_nantes <= 55


def test_missing_coords(cfg):
    [i] = imp.select([_etab(x=None, y=None)], cfg)
    assert i.km_nantes is None


def test_fetch_paginates(monkeypatch):
    import httpx

    pages = {"*": ("c1", [{"siret": "1"}]), "c1": ("c1", [{"siret": "2"}])}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["X-INSEE-Api-Key-Integration"] == "k"
        cur = req.url.params["curseur"]
        nxt, etabs = pages[cur]
        return httpx.Response(200, json={"header": {"curseurSuivant": nxt}, "etablissements": etabs})

    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    out = imp.fetch("k", "44", date(2026, 7, 1), date(2026, 9, 24))
    assert [e["siret"] for e in out] == ["1", "2"]


def test_fetch_requires_key():
    with pytest.raises(RuntimeError):
        imp.fetch("", "44", date(2026, 7, 1), date(2026, 9, 24))
