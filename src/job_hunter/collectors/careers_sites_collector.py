"""Collecteur sites carrières — v1 : scraper Manitou uniquement.

Reco du 04/07/2026 sur les 5 employeurs prioritaires : CA-GIP et Arkéa bloqués
edge (anti-bot, robots.txt inclus — on ne contourne pas), Groupama et VYV/Harmonie
= SPA JS (API à capturer en DevTools, v2). Ces employeurs restent couverts par
France Travail / JobSpy, où le matching tier du scoring les repérera.

Déviation assumée vs brief : pas de filtre 48 h ici — une offre encore en ligne
chez un employeur cible reste pertinente, et la dédup rend l'ingestion idempotente.
"""
import re
import time
from datetime import date
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx
import yaml
from loguru import logger
from selectolax.parser import HTMLParser, Node

from job_hunter.config import Settings
from job_hunter.models import Employer, RawJob

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
}
TIMEOUT_S = 15.0
RATE_LIMIT_S = 2.0  # 1 req / 2 s par domaine
EXCLUDED_CONTRACTS = {"Alternance", "Stage"}  # hors cible candidat

_FR_MONTHS = {
    "janvier": 1, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "août": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "décembre": 12,
}


def collect(settings: Settings) -> list[RawJob]:
    employers = load_employers(settings.employers_yaml)
    if not employers:
        logger.warning("employers.yaml vide ou absent, source careers_sites skippée")
        return []

    jobs: dict[str, RawJob] = {}
    # retries=2 au niveau transport : absorbe les erreurs de connexion transitoires (DNS, reset)
    transport = httpx.HTTPTransport(retries=2)
    with httpx.Client(
        timeout=TIMEOUT_S, headers=HEADERS, follow_redirects=True, transport=transport
    ) as client:
        for emp in employers:
            if not emp.careers_url:
                continue
            domain = urlparse(emp.careers_url).netloc
            # Dispatch : champ ats du YAML d'abord (scalable), domaine en héritage
            scraper = SCRAPERS_BY_ATS.get(emp.ats or "") or SCRAPERS.get(domain)
            if scraper is None:
                logger.warning(f"careers_site : pas de scraper pour {domain}, skippé")
                continue
            try:
                try:
                    found = scraper(client, emp)
                except httpx.TransportError:
                    time.sleep(3)  # DNS/proxy transitoire (vu sur Horizon le 05/07)
                    found = scraper(client, emp)
            except Exception as exc:  # noqa: BLE001 — un site cassé ne bloque pas le run
                logger.warning(f"careers_site[{emp.name}] : échec — {exc}")
                continue
            for job in found:
                jobs.setdefault(job.url, job)
            time.sleep(RATE_LIMIT_S)

    logger.info(f"careers_sites : {len(jobs)} offres")
    return list(jobs.values())


def load_employers(path: Path) -> list[Employer]:
    """Référentiel employeurs (réutilisé par le scoring tier en Phase 3)."""
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        logger.warning(f"employers.yaml malformé : {exc}")
        return []
    return [Employer(**e) for e in (data.get("employers") or [])]


# --- Manitou (WordPress rendu serveur) --------------------------------------


def _scrape_manitou(client: httpx.Client, emp: Employer) -> list[RawJob]:
    """Listing /fr/offres/ : liens d'offres + métadonnées (date, contrat, ville)
    extraites du texte du bloc englobant — volontairement sans classes CSS,
    plus robustes aux refontes de thème que des sélecteurs précis."""
    resp = client.get(emp.careers_url)
    resp.raise_for_status()
    tree = HTMLParser(resp.text)

    jobs: list[RawJob] = []
    seen: set[str] = set()
    for a in tree.css("a[href*='/fr/offres/']"):
        href = (a.attributes.get("href") or "").split("#")[0]
        # Écarte la page de listing elle-même et les liens de navigation
        if href.rstrip("/").endswith("/fr/offres") or href in seen:
            continue
        title = a.text(strip=True)
        if not title:
            continue
        seen.add(href)

        meta = _closest_block_text(a)
        contract = next(
            (c for c in ("CDI", "CDD", "Alternance", "Stage") if re.search(rf"\b{c}\b", meta)),
            None,
        )
        if contract in EXCLUDED_CONTRACTS:
            continue
        city = _city_after_france(meta)
        jobs.append(
            RawJob(
                source="careers_site",
                external_id=href,
                title=title,
                company=emp.name,
                location=f"{city}, France" if city else "France",
                contract_type=contract,
                salary_min=None,
                salary_max=None,
                remote_pct=None,
                description=None,  # listing seul ; pas de fetch par offre (38 requêtes évitées)
                url=href,
                posted_at=_parse_fr_date(meta),
                raw={"employer": emp.name, "meta_text": meta[:500]},
            )
        )
    return jobs


def _closest_block_text(node: Node, max_up: int = 4) -> str:
    """Texte du <li>/<article> englobant (ou à défaut du parent à max_up niveaux)."""
    current = node
    for _ in range(max_up):
        if current.parent is None:
            break
        current = current.parent
        if current.tag in ("li", "article"):
            break
    return current.text(separator="\n", strip=True)


def _parse_fr_date(text: str) -> date | None:
    m = re.search(r"(\d{1,2})\s+([a-zûé]+)\s+(\d{4})", text.lower())
    if not m or m.group(2) not in _FR_MONTHS:
        return None
    try:
        return date(int(m.group(3)), _FR_MONTHS[m.group(2)], int(m.group(1)))
    except ValueError:
        return None


def _city_after_france(text: str) -> str | None:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    for i, line in enumerate(lines):
        if line == "France" and i + 1 < len(lines):
            return lines[i + 1]
    return None


# --- Cegid « profils.org » : Arkéa + Docaposte (rendu serveur, parser partagé) --

_CEGID_DATE_RE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")
# Villes de la zone pour les filtres client des scrapers API (lower, sans accents stricts)
_ZONE_CITIES = (
    "nantes", "saint-herblain", "st-herblain", "carquefou", "ancenis",
    "saint-nazaire", "reze", "rezé", "orvault", "vertou",
)


def _scrape_cegid(client: httpx.Client, emp: Employer) -> list[RawJob]:
    """Moteur Cegid/« profils.org ». Constat 05/07/2026 : AUCUNE facette géographique
    sur ces sites (que métier/contrat) et le lieu est absent des tuiles. On liste donc
    tout (national), location vide — complétée par l'enrichissement page détail
    post-dédup ; le scorer pénalise les CP explicitement hors 44 (score 30)."""
    resp = client.get(emp.careers_url)
    resp.raise_for_status()
    tree = HTMLParser(resp.text)

    jobs: list[RawJob] = []
    seen: set[str] = set()
    for a in tree.css("a[href*='emploi-']"):
        href = (a.attributes.get("href") or "").split("#")[0]
        title = a.text(strip=True)
        if not title or ".aspx" not in href:
            continue
        url = str(httpx.URL(emp.careers_url).join(href))
        if url in seen:
            continue
        seen.add(url)
        meta = _closest_block_text(a)
        contract = next(
            (
                c
                for c in ("CDI", "CDD", "ALTERNANCE", "STAGE", "EMPLOI VACANCES")
                if re.search(rf"\b{re.escape(c)}\b", meta, re.I)
            ),
            None,
        )
        if contract not in (None, "CDI", "CDD"):
            continue
        m = _CEGID_DATE_RE.search(meta)
        posted = None
        if m:
            try:
                posted = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                posted = None
        jobs.append(
            RawJob(
                source="careers_site", external_id=url, title=title, company=emp.name,
                location="",  # rempli à l'enrichissement (page détail)
                contract_type=contract, salary_min=None, salary_max=None, remote_pct=None,
                description=None, url=url, posted_at=posted,
                raw={"employer": emp.name, "meta_text": meta[:500]},
            )
        )
    return jobs


# --- SmartRecruiters : SBS/groupe Sopra + Devoteam (API JSON publique) ---------


def _scrape_smartrecruiters(client: httpx.Client, emp: Employer) -> list[RawJob]:
    """API publique SmartRecruiters ; slug = 1er segment de careers_url.
    NB : le slug SopraSteria1 couvre TOUTES les marques du groupe Sopra —
    l'attribution se fait par le champ company du posting, et le tier-matching
    du scoring mappe ensuite via les aliases (SBS tier-1, Sopra Steria tier-2).
    Les params city/country de l'API ne sont pas garantis → filtre client aussi."""
    slug = httpx.URL(emp.careers_url).path.strip("/").split("/")[0]
    jobs: list[RawJob] = []
    offset = 0
    total_seen = 0
    for _ in range(10):  # cap 1000 postings parcourus
        resp = client.get(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
            # q est le seul filtre documenté fiable ; les params city/country testés le
            # 05/07 vidaient la réponse (fix post-STOP). Le filtre client fait foi.
            params={"q": "nantes", "limit": 100, "offset": offset},
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("content") or []
        total_seen += len(content)
        for p in content:
            loc = p.get("location") or {}
            city = (loc.get("city") or "").lower()
            cp44 = str(loc.get("postalCode") or "").startswith("44")
            remote = bool(loc.get("remote"))
            if not (remote or cp44 or any(z in city for z in _ZONE_CITIES)):
                continue
            pid = str(p.get("id") or "")
            title = (p.get("name") or "").strip()
            if not pid or not title:
                continue
            jobs.append(
                RawJob(
                    source="careers_site", external_id=pid, title=title,
                    company=((p.get("company") or {}).get("name") or emp.name).strip(),
                    location=f"{loc.get('city') or 'Remote'}, France",
                    contract_type=None,  # typeOfEmployment ≠ contrat FR
                    salary_min=None, salary_max=None,
                    remote_pct=100 if remote else None,
                    description=None,
                    url=f"https://jobs.smartrecruiters.com/{slug}/{pid}",
                    posted_at=_iso_date(p.get("releasedDate")),
                    raw=p,
                )
            )
        offset += len(content)
        if len(content) < 100 or offset >= int(data.get("totalFound") or 0):
            break
        time.sleep(1)
    logger.debug(
        f"careers_site[{emp.name}] : SR {slug} — {len(jobs)} en zone / "
        f"{total_seen} parcourues (totalFound={data.get('totalFound')})"
    )
    return jobs


# --- Teamtailor : Akeneo (JSON Feed public) ------------------------------------


def _scrape_teamtailor(client: httpx.Client, emp: Employer) -> list[RawJob]:
    """JSON Feed Teamtailor (<domaine>/jobs.json, confirmé HTTP 200 le 05/07/2026).
    Limite assumée : le feed n'expose pas le lieu → location vide (plancher
    géo-filtré 75). Akeneo étant basée à Nantes le bruit international attendu
    est faible ; à resserrer si des offres Boston/Düsseldorf polluent le Sheet."""
    u = httpx.URL(emp.careers_url)
    resp = client.get(f"{u.scheme}://{u.host}/jobs.json")
    resp.raise_for_status()
    jobs: list[RawJob] = []
    for it in resp.json().get("items") or []:
        url = (it.get("url") or "").strip()
        title = (it.get("title") or "").strip()
        if not url or not title:
            continue
        desc = HTMLParser(it.get("content_html") or "").text(separator=" ", strip=True) or None
        jobs.append(
            RawJob(
                source="careers_site", external_id=str(it.get("id") or url), title=title,
                company=emp.name, location="", contract_type=None,
                salary_min=None, salary_max=None, remote_pct=None,
                description=desc, url=url, posted_at=_iso_date(it.get("date_published")),
                raw={k: v for k, v in it.items() if k != "content_html"},
            )
        )
    return jobs


# --- Greenhouse : Horizon Trading (API JSON publique) ---------------------------


def _scrape_greenhouse(client: httpx.Client, emp: Employer) -> list[RawJob]:
    """API Greenhouse boards ; hôte .eu détecté depuis careers_url."""
    u = httpx.URL(emp.careers_url)
    slug = u.path.strip("/").split("/")[0]
    api_host = "boards-api.eu.greenhouse.io" if ".eu." in u.host else "boards-api.greenhouse.io"
    resp = client.get(f"https://{api_host}/v1/boards/{slug}/jobs")
    resp.raise_for_status()
    jobs: list[RawJob] = []
    for j in resp.json().get("jobs") or []:
        loc_name = ((j.get("location") or {}).get("name") or "").strip()
        low = loc_name.lower()
        if not any(z in low for z in (*_ZONE_CITIES, "remote", "télétravail", "teletravail")):
            continue
        title = (j.get("title") or "").strip()
        url = (j.get("absolute_url") or "").strip()
        if not title or not url:
            continue
        jobs.append(
            RawJob(
                source="careers_site", external_id=str(j.get("id") or url), title=title,
                company=emp.name, location=loc_name, contract_type=None,
                salary_min=None, salary_max=None,
                remote_pct=100 if "remote" in low else None,
                description=None, url=url, posted_at=_iso_date(j.get("updated_at")), raw=j,
            )
        )
    return jobs


# --- Inetum (rendu serveur, best effort) ----------------------------------------


def _scrape_inetum(client: httpx.Client, emp: Employer) -> list[RawJob]:
    """Listing mondial rendu serveur. Repérage par CONTENEUR (diagnostic 05/07 : le
    « France - Nantes » n'est pas à ≤3 parents du lien) : plus petit bloc (<600 car.)
    contenant la chaîne, puis lien le plus proche en remontant."""
    resp = client.get(emp.careers_url)
    resp.raise_for_status()
    tree = HTMLParser(resp.text)
    jobs: list[RawJob] = []
    seen: set[str] = set()
    for node in tree.css("div, li, article, p, h2, h3"):
        txt = node.text(separator=" ", strip=True)
        if not txt or len(txt) > 600 or "france - nantes" not in txt.lower():
            continue  # >600 caractères = conteneur de page, pas une carte d'offre
        link, current = None, node
        for _ in range(4):
            link = next((a for a in current.css("a") if a.attributes.get("href")), None)
            if link is not None or current.parent is None:
                break
            current = current.parent
        if link is None:
            continue
        href = (link.attributes.get("href") or "").split("#")[0]
        if not href:
            continue
        url = str(httpx.URL(emp.careers_url).join(href))
        if url in seen:
            continue
        seen.add(url)
        title = link.text(strip=True)
        if not title or title.lower() in ("lire la suite", "en savoir plus", "read more"):
            h = current.css_first("h2, h3, h4")
            title = h.text(strip=True) if h else ""
        if len(title) < 8:
            continue
        low = txt.lower()
        contract = "CDI" if "open-ended" in low else ("CDD" if "fixed-term" in low else None)
        jobs.append(
            RawJob(
                source="careers_site", external_id=url, title=title, company=emp.name,
                location="Nantes, France", contract_type=contract,
                salary_min=None, salary_max=None, remote_pct=None,
                description=None, url=url, posted_at=None,
                raw={"employer": emp.name, "block": txt[:300]},
            )
        )
    if not jobs:
        page_text = tree.body.text() if tree.body else ""
        hits = page_text.lower().count("france - nantes")
        logger.warning(
            f"careers_site[{emp.name}] : aucune offre « France - Nantes » reconnue "
            f"(occurrences texte : {hits}), skip"
        )
    return jobs


# --- Lever : SFEIR (API JSON publique) -------------------------------------------


def _scrape_lever(client: httpx.Client, emp: Employer) -> list[RawJob]:
    """API publique Lever : /v0/postings/{slug}?mode=json."""
    slug = httpx.URL(emp.careers_url).path.strip("/").split("/")[0]
    resp = client.get(
        f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json", "limit": 200}
    )
    resp.raise_for_status()
    jobs: list[RawJob] = []
    for p in resp.json() or []:
        loc = ((p.get("categories") or {}).get("location") or "").strip()
        low = loc.lower()
        if not any(z in low for z in (*_ZONE_CITIES, "remote")):
            continue
        title = (p.get("text") or "").strip()
        url = (p.get("hostedUrl") or "").strip()
        if not title or not url:
            continue
        try:
            posted = date.fromtimestamp(int(p.get("createdAt", 0)) / 1000)
        except (TypeError, ValueError, OSError):
            posted = None
        jobs.append(
            RawJob(
                source="careers_site", external_id=str(p.get("id") or url), title=title,
                company=emp.name, location=loc, contract_type=None,
                salary_min=None, salary_max=None,
                remote_pct=100 if "remote" in low else None,
                description=(p.get("descriptionPlain") or "").strip() or None,
                url=url, posted_at=posted,
                raw={k: v for k, v in p.items() if k not in ("description", "lists")},
            )
        )
    return jobs


def _iso_date(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


# Dispatch historique par domaine (entrées d'origine)
SCRAPERS: dict[str, Callable[[httpx.Client, Employer], list[RawJob]]] = {
    "careers.manitou-group.com": _scrape_manitou,
    "recrutement.arkea.com": _scrape_cegid,
    "docaposte-recrute.profils.org": _scrape_cegid,
    "careers.smartrecruiters.com": _scrape_smartrecruiters,  # SBS/groupe Sopra + Devoteam
    "careers.akeneo.com": _scrape_teamtailor,
    "job-boards.eu.greenhouse.io": _scrape_greenhouse,
    "www.inetum.com": _scrape_inetum,
}

# --- Moissonneur SSR générique (ats: ssr + link_pattern dans le YAML) ------------

_NAV_TITLES = {"lire la suite", "en savoir plus", "read more", "voir l'offre", "postuler", "candidater"}


def _scrape_ssr_generic(client: httpx.Client, emp: Employer) -> list[RawJob]:
    """Listing SSR quelconque : moissonne les liens dont le href contient
    emp.link_pattern. Titre = texte du lien ; contrat par bloc englobant ; lieu et
    description complétés par l'enrichissement post-dédup (CP hors 44 pénalisé au
    scoring). Page 1 seulement (les listings trient par date : la veille quotidienne
    rattrape le flux). Auto-diagnostic si 0 lien : échantillon des hrefs vus."""
    if not emp.link_pattern:
        logger.warning(f"careers_site[{emp.name}] : ats=ssr sans link_pattern, skip")
        return []
    resp = client.get(emp.careers_url)
    resp.raise_for_status()
    tree = HTMLParser(resp.text)
    listing_path = httpx.URL(emp.careers_url).path.rstrip("/")

    jobs: list[RawJob] = []
    seen: set[str] = set()
    matched_texts: list[str] = []  # diagnostic : liens matchés même si rejetés ensuite
    for a in tree.css("a"):
        href = (a.attributes.get("href") or "").split("#")[0].split("?")[0]
        if not href:
            continue
        url = str(httpx.URL(emp.careers_url).join(href))
        # Match sur l'URL ABSOLUE résolue : les hrefs relatifs («nos-offres/x»)
        # ne matchaient pas un pattern «/nos-offres/» (bug calibrage 05/07)
        if emp.link_pattern not in url:
            continue
        if httpx.URL(url).path.rstrip("/") == listing_path or url in seen:
            continue
        title = a.text(strip=True)
        matched_texts.append(title or "∅")
        if not title:  # lien-image : titre dans le heading du bloc englobant
            current = a
            for _ in range(3):
                if current.parent is None:
                    break
                current = current.parent
                h = current.css_first("h2, h3, h4")
                if h is not None:
                    title = h.text(strip=True)
                    break
        if not title or len(title) < 8 or title.lower() in _NAV_TITLES:
            # Dernier recours : le slug de l'URL porte souvent le titre
            # (Flatchr «…/vacancy/1234-assistant-data-engineer», Drupal, etc.)
            title = _title_from_slug(url)
        if not title or len(title) < 8:
            continue
        seen.add(url)
        meta = _closest_block_text(a)
        # Pas de filtre stage/alternance ici : le bloc englobant peut contenir le menu
        # « Stage/Alternance » du site et tuer toutes les offres (vu sur mc2i le 06/07).
        # L'exclusion contrat est faite en aval sur le TITRE (base.is_excluded_contract).
        contract = next(
            (c for c in ("CDI", "CDD") if re.search(rf"\b{c}\b", meta, re.I)), None
        )
        jobs.append(
            RawJob(
                source="careers_site", external_id=url, title=title, company=emp.name,
                location="",  # rempli à l'enrichissement
                contract_type=contract, salary_min=None, salary_max=None, remote_pct=None,
                description=None, url=url, posted_at=_parse_fr_date(meta),
                raw={"employer": emp.name, "meta_text": meta[:300]},
            )
        )
    if not jobs:
        if matched_texts:  # les liens matchent mais tous rejetés : problème de TITRE
            logger.warning(
                f"careers_site[{emp.name}] : {len(matched_texts)} lien(s) matchent "
                f"'{emp.link_pattern}' mais 0 retenu — textes de liens : "
                f"{[t[:40] for t in matched_texts[:10]]}"
            )
        else:
            sample = sorted({(a.attributes.get("href") or "")[:70] for a in tree.css("a") if a.attributes.get("href")})[:15]
            logger.warning(
                f"careers_site[{emp.name}] : 0 lien matchant '{emp.link_pattern}' — hrefs vus : {sample}"
            )
    return jobs


def _title_from_slug(url: str) -> str:
    """«…/vacancy/1234-assistant-data-engineer» → «Assistant data engineer»."""
    seg = httpx.URL(url).path.rstrip("/").split("/")[-1]
    seg = re.sub(r"\.(html?|aspx)$", "", seg)
    for _ in range(2):  # ids numériques/hex en tête de slug
        seg = re.sub(r"^[\da-f]{2,}-", "", seg)
    title = seg.replace("-", " ").replace("_", " ").strip()
    return title[:1].upper() + title[1:] if title else ""


# Dispatch par ATS (champ `ats` du YAML) — scalable : un nouvel employeur sur un
# ATS supporté = une entrée YAML, zéro code
SCRAPERS_BY_ATS: dict[str, Callable[[httpx.Client, Employer], list[RawJob]]] = {
    "smartrecruiters": _scrape_smartrecruiters,
    "teamtailor": _scrape_teamtailor,
    "greenhouse": _scrape_greenhouse,
    "lever": _scrape_lever,
    "cegid": _scrape_cegid,
    "ssr": _scrape_ssr_generic,
}


# --- Enrichissement post-dédup ----------------------------------------------


def enrich_descriptions(jobs: list[RawJob]) -> None:
    """Enrichit les offres careers NOUVELLES sans description (post-dédup, une seule
    fois par offre à vie) : hard/soft skills scorent sur du vrai texte — sans ça, la
    passe du 05/07 collectait 121 offres et n'en retenait AUCUNE (plafond ~61).

    Par défaut : extraction texte générique de la page détail (tous les sites v2 sont
    SSR : Manitou, Cegid, Greenhouse, Inetum). Cas particulier SmartRecruiters : la
    page publique est JS → API posting detail (jobAd). Complète aussi location quand
    vide (Cegid : lieu absent des tuiles). Échec = titre seul, jamais bloquant."""
    transport = httpx.HTTPTransport(retries=2)
    enriched = 0
    with httpx.Client(
        timeout=TIMEOUT_S, headers=HEADERS, follow_redirects=True, transport=transport
    ) as client:
        for job in jobs:
            try:
                if urlparse(job.url).netloc == "jobs.smartrecruiters.com":
                    job.description = _fetch_sr_description(client, job.url)
                else:
                    job.description = _fetch_page_text(client, job.url)
                enriched += job.description is not None
                if not job.location and job.description:
                    job.location = _extract_location(job.description)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"careers_site : description non récupérée ({job.url}) — {exc}")
            time.sleep(1.0)
    if jobs:
        logger.info(f"careers_site : {enriched}/{len(jobs)} descriptions enrichies")


def _fetch_page_text(client: httpx.Client, url: str) -> str | None:
    """Texte brut du contenu principal d'une page détail SSR : suffisant pour du
    scoring par regex, le bruit résiduel (nav/cookies) est marginal et borné."""
    resp = client.get(url)
    resp.raise_for_status()
    tree = HTMLParser(resp.text)
    node = tree.css_first("main") or tree.css_first("article") or tree.body
    return node.text(separator=" ", strip=True)[:20000] if node else None


def _fetch_sr_description(client: httpx.Client, url: str) -> str | None:
    """SmartRecruiters : GET /v1/companies/{slug}/postings/{id} → jobAd.sections."""
    parts = httpx.URL(url).path.strip("/").split("/")
    if len(parts) < 2:
        return None
    resp = client.get(
        f"https://api.smartrecruiters.com/v1/companies/{parts[0]}/postings/{parts[1]}"
    )
    resp.raise_for_status()
    sections = (resp.json().get("jobAd") or {}).get("sections") or {}
    texts = [
        t
        for sec in sections.values()
        if isinstance(sec, dict)
        and (t := HTMLParser(sec.get("text") or "").text(separator=" ", strip=True))
    ]
    return " ".join(texts)[:20000] or None


def _extract_location(text: str) -> str:
    """Lieu depuis le texte de la page détail (offres Cegid : lieu absent des tuiles).
    Priorité aux villes de la zone, puis CP suivi d'un nom de ville capitalisé (évite
    les faux positifs type « 35000 collaborateurs »). Rien de sûr → '' (plancher)."""
    low = text.lower()
    for city in _ZONE_CITIES:
        if city in low:
            return city.title()
    if "loire-atlantique" in low or "loire atlantique" in low:
        return "Loire-Atlantique"
    m = re.search(r"\b(\d{5})\s+[A-ZÉÈÀ][a-zé]", text)
    if m:
        return m.group(1)  # CP brut : le scorer pénalise les CP hors 44
    return ""
