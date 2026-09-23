"""Collecteur alertes mail : LinkedIn, Hellowork, Indeed (alertes), lus en IMAP.

Couvre les plateformes sans API ni scraping possible (LinkedIn, Hellowork). Les
alertes arrivent (directement ou par transfert Gmail) sur une boîte dédiée, lue en
IMAP avec un mot de passe d'application.

Principes :
- Lecture seule : SELECT readonly + BODY.PEEK → aucun mail marqué lu, déplacé, supprimé.
- Sans état : on relit les ALERTS_LOOKBACK_DAYS derniers jours à chaque run ; la dédup
  (fingerprint entreprise+titre) absorbe les répétitions. Un run raté ne perd rien.
- Un parseur par gabarit, fonctions pures testées sur des mails réels anonymisés.

Non couverts volontairement :
- APEC : déjà collectée par son API JSON (apec_rss_collector).
- Indeed « match » (donotreply@match.indeed.com) : recommandations hors alerte, bruitées
  (SAV sécurité incendie, M&A…). Seules les alertes (jobalert.indeed.com) sont lues.
- Annonces sponsorisées Indeed (liens /pagead/) : pas d'identifiant d'offre stable.
"""
import base64
import binascii
import email
import imaplib
import re
from datetime import date, timedelta
from email import policy
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from urllib.parse import parse_qs, urlparse

from loguru import logger
from selectolax.parser import HTMLParser

from job_hunter.config import Settings
from job_hunter.models import RawJob

IMAP_HOST = "imap.gmail.com"

# Expéditeur → parseur (clé = adresse exacte, en minuscules)
SENDERS = {
    "jobalerts-noreply@linkedin.com": "linkedin",
    "alerte@emails.hellowork.com": "hellowork",
    "donotreply@jobalert.indeed.com": "indeed",
}


def collect(settings: Settings) -> list[RawJob]:
    """Lit les alertes récentes de la boîte dédiée. Non configuré → [] avec warning."""
    if not settings.alerts_imap_user or not settings.alerts_imap_password:
        logger.warning("email_alerts : ALERTS_IMAP_USER / ALERTS_IMAP_PASSWORD absents, source skippée")
        return []

    since = (date.today() - timedelta(days=settings.alerts_lookback_days)).strftime("%d-%b-%Y")
    jobs: dict[str, RawJob] = {}
    n_mails = 0
    imap = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        # Secrets colles dans GitHub = souvent un \n final -> imaplib refuse (CR/LF).
        # Le mot de passe applicatif Gmail s affiche aussi avec des espaces.
        user = settings.alerts_imap_user.strip()
        pwd = "".join(settings.alerts_imap_password.split())
        imap.login(user, pwd)
        imap.select("INBOX", readonly=True)
        for sender in SENDERS:
            typ, data = imap.search(None, "SINCE", since, "FROM", f'"{sender}"')
            if typ != "OK" or not data or not data[0]:
                continue
            for num in data[0].split():
                typ, msg_data = imap.fetch(num, "(BODY.PEEK[])")
                if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                n_mails += 1
                msg = email.message_from_bytes(msg_data[0][1], policy=policy.default)
                for job in parse_message(msg):
                    jobs.setdefault(job.url, job)
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001 — fermeture best-effort
            pass

    logger.info(f"email_alerts : {len(jobs)} offres uniques extraites de {n_mails} mail(s)")
    return list(jobs.values())


def parse_message(msg: EmailMessage) -> list[RawJob]:
    """Dispatch par expéditeur. Expéditeur inconnu ou parseur en échec → [] (jamais bloquant)."""
    sender = parseaddr(msg.get("From", ""))[1].lower()
    kind = SENDERS.get(sender)
    if kind is None:
        return []
    posted = _msg_date(msg)
    try:
        if kind == "linkedin":
            return parse_linkedin(_body(msg, "plain"), posted)
        if kind == "hellowork":
            return parse_hellowork(_body(msg, "html"), posted)
        return parse_indeed(_body(msg, "plain"), posted)
    except Exception as exc:  # noqa: BLE001 — un gabarit qui change ne casse pas le run
        logger.warning(f"email_alerts[{kind}] : parsing échoué ({msg.get('Subject', '')[:60]}) — {exc}")
        return []


# --- LinkedIn (texte brut) ------------------------------------------------------
# Bloc type :  Titre / Entreprise / Lieu / [mentions diverses] / « Voir l’offre d’emploi : URL »

_LI_VIEW_RE = re.compile(r"Voir l.offre d.emploi\s*:\s*(\S+)")
_LI_JOBID_RE = re.compile(r"/jobs/view/(\d+)")


def parse_linkedin(text: str, posted: date | None) -> list[RawJob]:
    jobs: list[RawJob] = []
    for block in re.split(r"^\s*-{10,}\s*$", text, flags=re.MULTILINE):
        m = _LI_VIEW_RE.search(block)
        if not m:
            continue
        jid = _LI_JOBID_RE.search(m.group(1))
        if not jid:
            continue
        lines = [ln.strip() for ln in block[: m.start()].splitlines() if ln.strip()]
        # Le 1er bloc porte l'en-tête de l'alerte : on part de la fin (titre = 3e avant les mentions)
        lines = [ln for ln in lines if not _LI_NOISE_RE.search(ln)]
        if len(lines) < 3:
            continue
        title, company, location = lines[-3], lines[-2], lines[-1]
        jobs.append(
            _job("email_linkedin", jid.group(1), title, company, location,
                 f"https://www.linkedin.com/jobs/view/{jid.group(1)}/", posted)
        )
    return jobs


# Lignes de mention sous la carte (à écarter pour retrouver titre/entreprise/lieu)
_LI_NOISE_RE = re.compile(
    r"recrute activement|ancien(s)? coll[eè]gue|relation(s)?$|Postulez avec|"
    r"candidature simplifi|promu|Votre alerte Emploi|correspond(ent)? à vos préférences",
    re.IGNORECASE,
)


# --- Hellowork (HTML) -----------------------------------------------------------
# Lignes texte d'une carte : Titre / Entreprise / [badge] / « Ville - 44 » / Contrat /
# [Salaire] / « Voir l’offre ». Le mail répète les offres (liste + « publiées
# dernièrement ») → dédup par (titre, entreprise, lieu).

_HW_LOC_RE = re.compile(r"^.+ - (\d{2,3}|2[AB])$")
_HW_ZONE_DEPTS = {"44", "49", "85", "35"}  # Nantes + ~50 km, comme le collecteur APEC
_HW_SALARY_RE = re.compile(r"([\d\s  ]+)\s*-\s*([\d\s  ]+)\s*€\s*/\s*an")


def parse_hellowork(html: str, posted: date | None) -> list[RawJob]:
    tree = HTMLParser(html)
    # Liens de titre dans l'ordre du document, par intitulé : deux cartes au même titre
    # (« Chef de projet informatique » chez deux ESN) gardent chacune leur lien.
    title_links: dict[str, list[str]] = {}
    for a in tree.css("a"):
        txt = " ".join(a.text(separator=" ").split())
        href = a.attributes.get("href") or ""
        if "/clic/" in href and txt and not txt.lower().startswith("voir "):
            title_links.setdefault(txt, []).append(href)
    link_idx: dict[str, int] = {}

    body = tree.body.text(separator="\n") if tree.body else ""
    lines = [" ".join(ln.split()) for ln in body.split("\n") if ln.strip()]
    jobs: list[RawJob] = []
    seen: set[tuple[str, str, str]] = set()
    i = 0
    while i < len(lines):
        title = lines[i]
        if title not in title_links:
            i += 1
            continue
        # Lien de la n-ième occurrence du titre (consommé avant tout « continue »)
        k = link_idx.get(title, 0)
        link_idx[title] = k + 1
        href = title_links[title][min(k, len(title_links[title]) - 1)]
        # Fenêtre de la carte : jusqu'au prochain « Voir l’offre »
        j = i + 1
        card: list[str] = []
        while j < len(lines) and not lines[j].lower().startswith("voir l"):
            card.append(lines[j])
            j += 1
        i = j + 1
        if not card:
            continue
        company = card[0]
        loc_m = next((m for c in card[1:] if (m := _HW_LOC_RE.match(c))), None)
        if loc_m is None:
            continue  # pas une carte d'offre (liens de pied de mail : « Espace candidat »…)
        if loc_m.group(1) not in _HW_ZONE_DEPTS:
            continue  # offres « publiées dernièrement » hors zone (Dijon, Yvelines…)
        location = loc_m.group(0)
        contract = next((c for c in card[1:] if c in ("CDI", "CDD", "Intérim", "Stage", "Alternance", "Freelance")), None)
        smin, smax = _hw_salary(next((c for c in card if "€" in c), ""))
        key = (title, company, location)
        if key in seen:
            continue
        seen.add(key)
        url = _hw_canonical_url(href)
        jobs.append(
            _job("email_hellowork", f"{company}|{title}|{location}", title, company, location, url,
                 posted, contract=contract, smin=smin, smax=smax)
        )
    return jobs


_HW_OFFER_RE = re.compile(r"https://www\.hellowork\.com/fr-fr/emplois/\d+\.html")


def _hw_canonical_url(href: str) -> str:
    """Lien de tracking Hellowork → URL de l'offre, sans tracking.

    Format : .../clic/<id>/<n>/<hash>/<base64 url-safe de « email🪢URL?utm_… »>. Le lien
    change à chaque mail (et embarque l'adresse du destinataire) : inutilisable comme clé
    de dédup Sheet. Décodage impossible → lien d'origine (jamais bloquant).
    """
    token = href.rstrip("/").rsplit("/", 1)[-1]
    try:
        decoded = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode("utf-8", "replace")
    except (binascii.Error, ValueError):
        return href
    m = _HW_OFFER_RE.search(decoded)
    return m.group(0) if m else href


def _hw_salary(s: str) -> tuple[int | None, int | None]:
    m = _HW_SALARY_RE.search(s)
    if not m:
        return None, None
    lo, hi = (int(re.sub(r"\D", "", g)) // 1000 for g in m.groups())
    return (lo, hi) if 15 <= lo <= hi <= 200 else (None, None)


# --- Indeed alertes (texte brut) ------------------------------------------------
# Bloc type : Titre / « Entreprise - Lieu » / [salaire] / [badges] / extrait / âge / URL (jk=…)

_IND_URL_RE = re.compile(r"^https://\S*indeed\.com/\S+$")
_IND_SALARY_RE = re.compile(r"De ([\d\s  ]+) € à ([\d\s  ]+) € par an")


def parse_indeed(text: str, posted: date | None) -> list[RawJob]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    start = next((k + 1 for k, ln in enumerate(lines) if ln.startswith("See matching results")), 0)
    jobs: list[RawJob] = []
    block: list[str] = []
    for ln in lines[start:]:
        if ln.startswith("Ne partagez pas"):
            break
        if not _IND_URL_RE.match(ln):
            block.append(ln)
            continue
        jk = parse_qs(urlparse(ln).query).get("jk", [None])[0]
        if jk and len(block) >= 2 and " - " in block[1]:
            title = block[0]
            company, _, location = block[1].rpartition(" - ")
            smin, smax = _ind_salary(" ".join(block))
            jobs.append(
                _job("email_indeed", jk, title, company, location,
                     f"https://fr.indeed.com/viewjob?jk={jk}", posted, smin=smin, smax=smax)
            )
        block = []  # URL = fin de bloc (y compris les annonces /pagead/ sans jk, ignorées)
    return jobs


def _ind_salary(s: str) -> tuple[int | None, int | None]:
    m = _IND_SALARY_RE.search(s)
    if not m:
        return None, None
    lo, hi = (int(re.sub(r"\D", "", g)) // 1000 for g in m.groups())
    return (lo, hi) if 15 <= lo <= hi <= 200 else (None, None)


# --- helpers --------------------------------------------------------------------


def _job(source, external_id, title, company, location, url, posted, *,
         contract=None, smin=None, smax=None) -> RawJob:
    return RawJob(
        source=source, external_id=str(external_id), title=title, company=company or "Anonyme",
        location=location, contract_type=contract, salary_min=smin, salary_max=smax,
        remote_pct=100 if re.search(r"t[ée]l[ée]travail|remote", location, re.I) else None,
        description=None, url=url, posted_at=posted, raw={},
    )


def _body(msg: EmailMessage, subtype: str) -> str:
    part = msg.get_body(preferencelist=(subtype,))
    return part.get_content() if part is not None else ""


def _msg_date(msg: EmailMessage) -> date | None:
    """Date du mail d'alerte ≈ date de publication (à 1-2 jours près)."""
    try:
        return parsedate_to_datetime(msg["Date"]).date()
    except (TypeError, ValueError):
        return None
