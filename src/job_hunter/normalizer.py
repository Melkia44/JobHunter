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


def compute_fingerprint(company: str, title: str) -> str:
    """Dédup par contenu (pas par id source) : attrape les reposts et le multi-source."""
    return hashlib.sha256(f"{normalize(company)}|{normalize(title)}".encode()).hexdigest()
