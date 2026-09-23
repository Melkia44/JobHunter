"""Normalisation de texte et fingerprint de dédup (brief §10)."""
import hashlib
import re
import unicodedata


def normalize(s: str) -> str:
    s = s.lower()
    # Accents
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    # H/F, F/H, (H/F), M/F…
    s = re.sub(r"\s*[\(\[]?\s*[hfm]\s*/\s*[hfm]\s*[\)\]]?\s*", " ", s)
    # Espaces multiples
    s = re.sub(r"\s+", " ", s).strip()
    # Ponctuation finale (+ re-strip : la ponctuation peut laisser un espace)
    return s.rstrip(".,;:!?").strip()


# Mots sans valeur distinctive dans un nom d'employeur (« Groupe SYD » = « SYD GROUPE »)
_COMPANY_NOISE = {"groupe", "group", "sa", "sas", "sasu", "france", "fr"}
# Marqueur de genre dans l'intitulé brut : (H/F), F/H, H/F/X, (All Gender)…
_GENDER_MARK_RE = re.compile(
    r"[\(\[]?\s*\b[hfm]\s*/\s*[hfm](\s*/\s*[a-z]+)?\b\s*[\)\]]?|\(all gender\)", re.I
)
_TIER_RE = re.compile(r"\s*\(p\d\)\s*$")


def canonical_company(company: str) -> str:
    """« Groupe SYD » / « SYD GROUPE » / « MANITOU GROUP (P1) » → jeton triés sans bruit."""
    tokens = re.findall(r"[a-z0-9]+", _TIER_RE.sub("", normalize(company)))
    kept = sorted(t for t in tokens if t not in _COMPANY_NOISE)
    return " ".join(kept or tokens)


def canonical_title(title: str) -> str:
    """Intitulé coupé au marqueur de genre : ce qui suit est du décor d'annonce
    (« … H/F Consortia - Testing, Développement… », « (F/H) – Nantes »)."""
    m = _GENDER_MARK_RE.search(title)
    head = title[: m.start()] if m and m.start() > 0 else title
    return normalize(head).rstrip(" -–—·|,")


def compute_fingerprint(company: str, title: str) -> str:
    """Dédup par contenu (pas par id source) : attrape les reposts et le multi-source.
    v2 (23/09/2026) : employeur et intitulé canonisés — doublons vus dans la Sheet :
    Groupe SYD / SYD GROUPE, « Product Owner Senior H/F » avec ou sans suffixe Consortia."""
    key = f"{canonical_company(company)}|{canonical_title(title)}"
    return hashlib.sha256(key.encode()).hexdigest()


def legacy_fingerprint(company: str, title: str) -> str:
    """Empreinte v1 (avant 23/09/2026), encore présente dans seen_jobs.db : consultée
    en plus de la v2 pour ne pas faire ressortir comme nouvelles les offres déjà vues."""
    return hashlib.sha256(f"{normalize(company)}|{normalize(title)}".encode()).hexdigest()
