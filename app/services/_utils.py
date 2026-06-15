"""Utilitare partajate intre providerii de cautare.

Aceste functii erau duplicate (copy-paste) in search_anthropic / search_tavily /
search_searxng / search_ollama / search_author. Le-am consolidat aici ca un fix
de regex sau o noua lista de domenii sa se faca intr-un singur loc.
"""
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Watermark-uri (IEEE / biblioteci universitare)
# ---------------------------------------------------------------------------
# Ex: "Authorized licensed use limited to: ... Downloaded on ... UTC from ...
#      Restrictions apply."
_WATERMARK_RE = re.compile(
    r'Authorized licensed use limited to:[^.]+\.\s*Downloaded on[^.]+\.\s*'
    r'(?:UTC\s*)?(?:from[^.]+\.)?\s*Restrictions apply\.?',
    flags=re.IGNORECASE,
)


def strip_watermarks(text: str) -> str:
    """Elimina watermark-urile de tip 'Authorized licensed use limited to: ...'."""
    return _WATERMARK_RE.sub('', text or '').strip()


# ---------------------------------------------------------------------------
# Parsare data — superset al tuturor variantelor folosite anterior
# ---------------------------------------------------------------------------
# Perechile (lungime_reala_data, format) — len(fmt) != lungimea datei reale.
# Ordinea conteaza: de la cel mai specific (ISO cu ora) la cel mai general (an).
_DATE_CANDIDATES = [
    (20, "%Y-%m-%dT%H:%M:%SZ"),
    (19, "%Y-%m-%dT%H:%M:%S"),
    (10, "%Y-%m-%d"),
    (7,  "%Y-%m"),
    (4,  "%Y"),
]


def parse_date(s) -> Optional[datetime]:
    """Parseaza o data dintr-un string in formate uzuale; None daca nu se potriveste."""
    if not s:
        return None
    text = str(s).strip()
    for length, fmt in _DATE_CANDIDATES:
        try:
            return datetime.strptime(text[:length], fmt)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Domenii academice
# ---------------------------------------------------------------------------
# Lista consolidata (superset din search_tavily + search_searxng).
ACADEMIC_DOMAINS = [
    # Preprint / Open Access
    "arxiv.org", "biorxiv.org", "medrxiv.org", "plos.org", "frontiersin.org",
    "mdpi.com", "elifesciences.org", "zenodo.org", "scielo.org",
    # Baze de date rezumate
    "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov", "nih.gov",
    "europepmc.org", "semanticscholar.org",
    # Edituri mari
    "nature.com", "science.org", "cell.com", "springer.com", "wiley.com",
    "tandfonline.com", "sciencedirect.com", "academic.oup.com",
    "jamanetwork.com", "nejm.org", "thelancet.com",
    # Tehnic / CS
    "ieee.org", "acm.org", "iopscience.iop.org",
    # Chimie / Energie
    "rsc.org", "pubs.acs.org", "pubs.rsc.org",
    # Retele academice
    "researchgate.net", "academia.edu",
    # Energie / Hidrogen / Electroliza
    "ecs.org", "ecst.ecsdl.org",                         # Electrochemical Society
    "nrel.gov",                                           # National Renewable Energy Lab
    "energy.gov", "hydrogen.energy.gov",                  # US Dept of Energy
    "irena.org",                                          # Int'l Renewable Energy Agency
    "chemrxiv.org",                                       # Preprint chimie
    "biomedcentral.com",                                  # BioMed Central open access
    "core.ac.uk",                                         # Agregator open access
    # Rezolvatori DOI / agregatori — frecvente in SearXNG science
    "doi.org", "dx.doi.org", "hdl.handle.net",
    "ieeexplore.ieee.org", "link.springer.com",
    "onlinelibrary.wiley.com", "dl.acm.org",
    "hal.science", "hal.archives-ouvertes.fr",
]


def domain(url: str) -> str:
    """Extrage domeniul (fara 'www.') dintr-un URL; '' daca esueaza."""
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def is_academic(url: str) -> bool:
    """True daca domeniul URL-ului e in lista academica (sau subdomeniu al ei)."""
    d = domain(url)
    return any(d == a or d.endswith("." + a) for a in ACADEMIC_DOMAINS)
