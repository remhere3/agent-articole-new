"""Teste de nivel 2 pentru providerul `author` (OpenAlex + CrossRef) cu respx.

Apelurile HTTP catre api.openalex.org / api.crossref.org sunt interceptate si
li se intoarce un raspuns fix — testam logica NOASTRA de parsare / filtrare /
deduplicare, fara internet, fara cost, deterministic.
"""
import httpx
import pytest
import respx

from app.services.search_author import search_articles

OA_AUTHORS = r"^https://api\.openalex\.org/authors"
OA_WORKS = r"^https://api\.openalex\.org/works"
CR_WORKS = r"^https://api\.crossref\.org/works"


def _oa_authors(display_name="Simona Raboaca", author_id="A123"):
    return {"results": [{"id": f"https://openalex.org/{author_id}", "display_name": display_name}]}


def _oa_works(works):
    return {"results": works, "meta": {"next_cursor": None}}


def _cr_works(items):
    return {"message": {"items": items, "next-cursor": None}}


def _oa_work(title, date, authors=("Simona Raboaca",)):
    return {
        "id": "https://openalex.org/W1",
        "title": title,
        "publication_date": date,
        "doi": "https://doi.org/10.1234/abc",
        "authorships": [{"author": {"display_name": a}} for a in authors],
        "primary_location": {"source": {"display_name": "Journal of Tests"}},
        "abstract_inverted_index": {"Rezumat": [0], "scurt": [1]},
    }


@respx.mock
@pytest.mark.asyncio
async def test_parseaza_un_work_openalex():
    respx.get(url__regex=OA_AUTHORS).mock(return_value=httpx.Response(200, json=_oa_authors()))
    respx.get(url__regex=OA_WORKS).mock(return_value=httpx.Response(
        200, json=_oa_works([_oa_work("Senzori optici avansati", "2024-06-01")])
    ))
    respx.get(url__regex=CR_WORKS).mock(return_value=httpx.Response(200, json=_cr_works([])))

    out = await search_articles("Simona Răboacă", days_back=3650, max_works=10)

    assert len(out) == 1
    art = out[0]
    assert art["title"] == "Senzori optici avansati"
    assert art["url"] == "https://doi.org/10.1234/abc"
    assert art["published_date"] == "2024-06-01"
    assert art["source"] == "Journal of Tests"
    assert art["summary"] == "Rezumat scurt"
    assert "_api" not in art  # cheia interna trebuie scoasa la final


@respx.mock
@pytest.mark.asyncio
async def test_filtreaza_articol_mai_vechi_decat_cutoff():
    # days_back=30 -> cutoff acum ~30 zile; un work din 2000 trebuie eliminat
    # de verificarea pub_dt < cutoff din cod (chiar daca API-ul l-ar returna).
    respx.get(url__regex=OA_AUTHORS).mock(return_value=httpx.Response(200, json=_oa_authors()))
    respx.get(url__regex=OA_WORKS).mock(return_value=httpx.Response(
        200, json=_oa_works([_oa_work("Lucrare veche", "2000-01-01")])
    ))
    respx.get(url__regex=CR_WORKS).mock(return_value=httpx.Response(200, json=_cr_works([])))

    out = await search_articles("Simona Raboaca", days_back=30, max_works=10)
    assert out == []


@respx.mock
@pytest.mark.asyncio
async def test_deduplica_acelasi_titlu_din_oa_si_cr():
    respx.get(url__regex=OA_AUTHORS).mock(return_value=httpx.Response(200, json=_oa_authors()))
    respx.get(url__regex=OA_WORKS).mock(return_value=httpx.Response(
        200, json=_oa_works([_oa_work("Lucrare Comuna", "2024-05-01")])
    ))
    respx.get(url__regex=CR_WORKS).mock(return_value=httpx.Response(200, json=_cr_works([
        {  # acelasi titlu ca in OA -> trebuie deduplicat
            "title": ["Lucrare Comuna"],
            "author": [{"given": "Simona", "family": "Raboaca"}],
            "published": {"date-parts": [[2024, 5, 1]]},
            "DOI": "10.9/dup",
        },
        {  # titlu unic doar in CR -> trebuie pastrat
            "title": ["Doar In CrossRef"],
            "author": [{"given": "Simona", "family": "Raboaca"}],
            "published": {"date-parts": [[2024, 4, 1]]},
            "DOI": "10.9/cr",
        },
    ])))

    out = await search_articles("Simona Raboaca", days_back=3650, max_works=10)
    titles = {a["title"] for a in out}
    assert titles == {"Lucrare Comuna", "Doar In CrossRef"}
    # "Lucrare Comuna" trebuie sa vina din OA (prioritate) -> URL-ul DOI din OA
    comuna = next(a for a in out if a["title"] == "Lucrare Comuna")
    assert comuna["url"] == "https://doi.org/10.1234/abc"


@respx.mock
@pytest.mark.asyncio
async def test_crossref_filtreaza_autor_nepotrivit():
    # CR poate intoarce lucrari ale altui autor pe potrivire vaga -> _name_matches
    # trebuie sa le elimine (nu apare 'Raboaca' printre autori).
    respx.get(url__regex=OA_AUTHORS).mock(return_value=httpx.Response(200, json=_oa_authors()))
    respx.get(url__regex=OA_WORKS).mock(return_value=httpx.Response(200, json=_oa_works([])))
    respx.get(url__regex=CR_WORKS).mock(return_value=httpx.Response(200, json=_cr_works([
        {
            "title": ["Articol de alt autor"],
            "author": [{"given": "Ion", "family": "Popescu"}],
            "published": {"date-parts": [[2024, 5, 1]]},
            "DOI": "10.9/other",
        },
    ])))

    out = await search_articles("Simona Raboaca", days_back=3650, max_works=10)
    assert out == []


@respx.mock
@pytest.mark.asyncio
async def test_eroare_openalex_nu_arunca_foloseste_crossref(monkeypatch):
    # OpenAlex pica (500) -> providerul trebuie sa continue cu rezultatele CrossRef
    # (asyncio.gather cu return_exceptions + fallback la []).
    # 500 e status retryable, deci _get_with_retry reincerca cu backoff; anulam
    # sleep-ul ca testul sa nu astepte real cele 2s+4s.
    import app.services._utils as _utils

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(_utils.asyncio, "sleep", _no_sleep)
    respx.get(url__regex=OA_AUTHORS).mock(return_value=httpx.Response(500))
    respx.get(url__regex=OA_WORKS).mock(return_value=httpx.Response(500))
    respx.get(url__regex=CR_WORKS).mock(return_value=httpx.Response(200, json=_cr_works([
        {
            "title": ["Salvat de CrossRef"],
            "author": [{"given": "Simona", "family": "Raboaca"}],
            "published": {"date-parts": [[2024, 5, 1]]},
            "DOI": "10.9/cr",
        },
    ])))

    out = await search_articles("Simona Raboaca", days_back=3650, max_works=10)
    assert len(out) == 1
    assert out[0]["title"] == "Salvat de CrossRef"
    assert out[0]["url"] == "https://doi.org/10.9/cr"


@respx.mock
@pytest.mark.asyncio
async def test_ambele_surse_jos_arunca_provider_down(monkeypatch):
    # OpenAlex SI CrossRef pica amandoua (5xx) -> providerul e indisponibil:
    # trebuie sa arunce ProviderDownError (nu sa intoarca [] tacut), ca breaker-ul
    # sa numere esecul. Anulam sleep-ul de retry ca testul sa nu astepte real.
    import app.services._utils as _utils
    from app.services._circuit import ProviderDownError

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(_utils.asyncio, "sleep", _no_sleep)
    respx.get(url__regex=OA_AUTHORS).mock(return_value=httpx.Response(503))
    respx.get(url__regex=CR_WORKS).mock(return_value=httpx.Response(503))

    with pytest.raises(ProviderDownError):
        await search_articles("Simona Raboaca", days_back=3650, max_works=10)
