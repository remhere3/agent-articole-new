"""Teste unitare pentru app/services/_utils.py.

Acopera functiile pure (parsare data, watermark, domenii, potrivire nume) plus
logica de reincercare `retry_async`. Toate testele ruleaza fara retea / DB /
chei API. Pentru `retry_async`, `asyncio.sleep` e inlocuit (monkeypatch) ca sa
nu astepte secunde reale.

Rulare:  ./venv/bin/pytest -q
"""
from datetime import datetime

import httpx
import pytest

from app.services import _utils


# ===========================================================================
# parse_date
# ===========================================================================
class TestParseDate:
    def test_iso_cu_ora_si_z(self):
        assert _utils.parse_date("2026-06-19T14:30:00Z") == datetime(2026, 6, 19, 14, 30, 0)

    def test_iso_cu_ora_fara_z(self):
        assert _utils.parse_date("2026-06-19T14:30:00") == datetime(2026, 6, 19, 14, 30, 0)

    def test_doar_data(self):
        assert _utils.parse_date("2024-03-15") == datetime(2024, 3, 15)

    def test_an_si_luna(self):
        assert _utils.parse_date("2024-03") == datetime(2024, 3, 1)

    def test_doar_an(self):
        assert _utils.parse_date("2024") == datetime(2024, 1, 1)

    def test_spatii_in_jur_sunt_ignorate(self):
        assert _utils.parse_date("  2024-03-15  ") == datetime(2024, 3, 15)

    @pytest.mark.parametrize("val", ["", None, "   ", 0])
    def test_gol_sau_falsy_da_none(self, val):
        assert _utils.parse_date(val) is None

    @pytest.mark.parametrize("val", ["data necunoscuta", "N/A", "15/03/2024", "March 2024"])
    def test_format_nerecunoscut_da_none(self, val):
        assert _utils.parse_date(val) is None

    def test_format_nerecunoscut_logheaza_debug_cu_valoarea_bruta(self, caplog):
        import logging
        with caplog.at_level(logging.DEBUG, logger="app.services._utils"):
            assert _utils.parse_date("data necunoscuta") is None
        assert any("data necunoscuta" in r.message for r in caplog.records)

    def test_input_gol_nu_logheaza(self, caplog):
        import logging
        with caplog.at_level(logging.DEBUG, logger="app.services._utils"):
            assert _utils.parse_date("") is None
        assert caplog.records == []

    def test_accepta_input_non_string(self):
        # functia face str(s) intern -> un int de an trebuie sa mearga
        assert _utils.parse_date(2024) == datetime(2024, 1, 1)

    def test_data_cu_text_suplimentar_dupa_lungime(self):
        # taie la 10 caractere ("%Y-%m-%d") => textul de dupa e ignorat
        assert _utils.parse_date("2024-03-15 plus gunoi") == datetime(2024, 3, 15)


# ===========================================================================
# strip_watermarks
# ===========================================================================
class TestStripWatermarks:
    def test_elimina_watermark_ieee_complet(self):
        text = (
            "Rezultate importante despre senzori. "
            "Authorized licensed use limited to: Univ Politehnica Bucuresti. "
            "Downloaded on January 15,2024 at 12:00:00 UTC from IEEE Xplore. "
            "Restrictions apply. Concluzii finale."
        )
        out = _utils.strip_watermarks(text)
        assert "Authorized licensed use" not in out
        assert "Restrictions apply" not in out
        assert "Rezultate importante despre senzori." in out
        assert "Concluzii finale." in out

    def test_case_insensitive(self):
        text = (
            "AUTHORIZED LICENSED USE LIMITED TO: x. "
            "DOWNLOADED ON y. RESTRICTIONS APPLY."
        )
        assert _utils.strip_watermarks(text) == ""

    def test_text_curat_ramane_neschimbat(self):
        text = "Text normal fara niciun watermark."
        assert _utils.strip_watermarks(text) == text

    def test_none_da_string_gol(self):
        assert _utils.strip_watermarks(None) == ""

    def test_string_gol(self):
        assert _utils.strip_watermarks("") == ""


# ===========================================================================
# domain
# ===========================================================================
class TestDomain:
    def test_extrage_domeniu_simplu(self):
        assert _utils.domain("https://arxiv.org/abs/1234") == "arxiv.org"

    def test_elimina_www(self):
        assert _utils.domain("https://www.nature.com/articles/x") == "nature.com"

    def test_pastreaza_subdomeniu(self):
        assert _utils.domain("https://ieeexplore.ieee.org/document/1") == "ieeexplore.ieee.org"

    def test_url_fara_schema(self):
        # fara schema, urlparse pune totul in path -> netloc gol
        assert _utils.domain("arxiv.org/abs/1") == ""

    def test_string_gol(self):
        assert _utils.domain("") == ""


# ===========================================================================
# is_academic  (foloseste lista incarcata din config/academic_domains.txt)
# ===========================================================================
class TestIsAcademic:
    def test_domeniu_academic_cunoscut(self):
        assert _utils.is_academic("https://arxiv.org/abs/2401.00001") is True

    def test_subdomeniu_al_unui_domeniu_academic(self):
        # ieee.org e in lista -> ieeexplore.ieee.org trebuie sa prinda
        assert _utils.is_academic("https://ieeexplore.ieee.org/document/1") is True

    def test_domeniu_neacademic(self):
        assert _utils.is_academic("https://example.com/blog") is False

    def test_potrivire_partiala_nu_trece(self):
        # "notarxiv.org" NU trebuie sa prinda pentru "arxiv.org"
        assert _utils.is_academic("https://notarxiv.org/x") is False

    def test_lista_de_domenii_nu_e_goala(self):
        # sanity check: fisierul de config s-a incarcat corect
        assert len(_utils.ACADEMIC_DOMAINS) > 0
        assert "arxiv.org" in _utils.ACADEMIC_DOMAINS


# ===========================================================================
# _load_academic_domains  (parsare fisier config)
# ===========================================================================
class TestLoadAcademicDomains:
    def test_ignora_comentarii_si_linii_goale(self, tmp_path):
        f = tmp_path / "domenii.txt"
        f.write_text(
            "# comentariu intreg\n"
            "\n"
            "arxiv.org\n"
            "nature.com  # comentariu inline\n"
            "   \n",
            encoding="utf-8",
        )
        assert _utils._load_academic_domains(f) == ["arxiv.org", "nature.com"]

    def test_normalizeaza_lowercase(self, tmp_path):
        f = tmp_path / "domenii.txt"
        f.write_text("ArXiv.ORG\n", encoding="utf-8")
        assert _utils._load_academic_domains(f) == ["arxiv.org"]

    def test_deduplica_pastrand_ordinea(self, tmp_path):
        f = tmp_path / "domenii.txt"
        f.write_text("b.org\na.org\nb.org\n", encoding="utf-8")
        assert _utils._load_academic_domains(f) == ["b.org", "a.org"]

    def test_fisier_inexistent_da_lista_goala(self, tmp_path):
        assert _utils._load_academic_domains(tmp_path / "nu_exista.txt") == []


# ===========================================================================
# looks_like_person_name
# ===========================================================================
class TestLooksLikePersonName:
    @pytest.mark.parametrize("nume", [
        "Roxana Ionete",
        "Nicolae Georgescu",
        "Roxana Elena Ionete",
        "Ștefan Câmpeanu",          # diacritice romanesti
    ])
    def test_nume_valide(self, nume):
        assert _utils.looks_like_person_name(nume) is True

    @pytest.mark.parametrize("text", [
        "machine learning",         # lowercase
        "Roxana",                   # un singur cuvant
        "AI in medicine 2024",      # cifre + cuvinte mici
        "a b c d e",                # 5 cuvinte (peste limita)
        "",
        "   ",
    ])
    def test_nu_sunt_nume(self, text):
        assert _utils.looks_like_person_name(text) is False


# ===========================================================================
# author_in_result
# ===========================================================================
class TestAuthorInResult:
    def test_prenume_si_nume_prezente_in_titlu(self):
        item = {"title": "Studiu de Roxana Ionete despre apa", "content": "", "summary": "", "authors": ""}
        assert _utils.author_in_result("Roxana Ionete", item) is True

    def test_ordine_inversata_nume_prenume(self):
        item = {"title": "", "content": "Ionete, Roxana et al.", "summary": "", "authors": ""}
        assert _utils.author_in_result("Roxana Ionete", item) is True

    def test_nume_complet_intermediar(self):
        item = {"title": "Roxana Elena Ionete - cercetare", "content": "", "summary": "", "authors": ""}
        assert _utils.author_in_result("Roxana Ionete", item) is True

    def test_doar_prenumele_prezent_nu_trece(self):
        item = {"title": "Articol de Roxana Popescu", "content": "", "summary": "", "authors": ""}
        assert _utils.author_in_result("Roxana Ionete", item) is False

    def test_potrivire_in_campul_authors_ca_lista(self):
        # SearXNG poate intoarce `authors` ca lista -> _as_text trebuie sa o aplatizeze
        item = {"title": "", "content": "", "summary": "", "authors": ["Roxana Ionete", "Alt Autor"]}
        assert _utils.author_in_result("Roxana Ionete", item) is True

    def test_nu_prinde_subsir_partial(self):
        # "Ion" nu trebuie sa prinda in "Ionete" (cere termen intreg)
        item = {"title": "Mihai Ionescu si Ana Ion", "content": "", "summary": "", "authors": ""}
        assert _utils.author_in_result("Roxana Ionete", item) is False

    def test_nume_dintr_un_singur_cuvant(self):
        item = {"title": "Lucrare de Einstein", "content": "", "summary": "", "authors": ""}
        assert _utils.author_in_result("Einstein", item) is True


# ===========================================================================
# is_retryable_http
# ===========================================================================
class TestIsRetryableHttp:
    def test_timeout_e_retryable(self):
        assert _utils.is_retryable_http(httpx.ReadTimeout("timeout")) is True

    def test_transport_error_e_retryable(self):
        assert _utils.is_retryable_http(httpx.ConnectError("connection refused")) is True

    @pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
    def test_status_tranzitoriu_e_retryable(self, code):
        req = httpx.Request("GET", "https://x.test")
        exc = httpx.HTTPStatusError("err", request=req, response=httpx.Response(code, request=req))
        assert _utils.is_retryable_http(exc) is True

    @pytest.mark.parametrize("code", [400, 401, 403, 404])
    def test_status_definitiv_nu_e_retryable(self, code):
        req = httpx.Request("GET", "https://x.test")
        exc = httpx.HTTPStatusError("err", request=req, response=httpx.Response(code, request=req))
        assert _utils.is_retryable_http(exc) is False

    def test_exceptie_generica_nu_e_retryable(self):
        assert _utils.is_retryable_http(ValueError("altceva")) is False


# ===========================================================================
# describe_exc
# ===========================================================================
class TestDescribeExc:
    def test_cu_mesaj(self):
        assert _utils.describe_exc(ValueError("boom")) == "ValueError: boom"

    def test_fara_mesaj_doar_tipul(self):
        # exceptii cu str() gol -> doar numele tipului
        assert _utils.describe_exc(httpx.ReadTimeout("")) == "ReadTimeout"


# ===========================================================================
# retry_async  (asyncio.sleep inlocuit ca sa nu astepte real)
# ===========================================================================
@pytest.fixture
def no_sleep(monkeypatch):
    """Inlocuieste asyncio.sleep din _utils cu un no-op (teste instant)."""
    async def _fake_sleep(_):
        return None
    monkeypatch.setattr(_utils.asyncio, "sleep", _fake_sleep)


class TestRetryAsync:
    @pytest.mark.asyncio
    async def test_succes_din_prima(self, no_sleep):
        apeluri = {"n": 0}

        async def fn():
            apeluri["n"] += 1
            return "ok"

        assert await _utils.retry_async(fn) == "ok"
        assert apeluri["n"] == 1

    @pytest.mark.asyncio
    async def test_reuseste_dupa_esecuri(self, no_sleep):
        apeluri = {"n": 0}

        async def fn():
            apeluri["n"] += 1
            if apeluri["n"] < 3:
                raise ValueError("tranzitoriu")
            return "ok"

        assert await _utils.retry_async(fn, attempts=3, retry_on=lambda e: True) == "ok"
        assert apeluri["n"] == 3

    @pytest.mark.asyncio
    async def test_arunca_dupa_epuizarea_incercarilor(self, no_sleep):
        apeluri = {"n": 0}

        async def fn():
            apeluri["n"] += 1
            raise ValueError("mereu esueaza")

        with pytest.raises(ValueError, match="mereu esueaza"):
            await _utils.retry_async(fn, attempts=3, retry_on=lambda e: True)
        assert apeluri["n"] == 3

    @pytest.mark.asyncio
    async def test_retry_on_false_arunca_imediat(self, no_sleep):
        apeluri = {"n": 0}

        async def fn():
            apeluri["n"] += 1
            raise ValueError("eroare definitiva")

        with pytest.raises(ValueError):
            await _utils.retry_async(fn, attempts=5, retry_on=lambda e: False)
        # retry_on respinge eroarea -> o singura incercare, fara reincercari
        assert apeluri["n"] == 1

    @pytest.mark.asyncio
    async def test_backoff_plafonat_la_max_delay(self, monkeypatch):
        # capturam intarzierile cerute ca sa verificam backoff-ul exponential plafonat
        delays = []

        async def _capture_sleep(d):
            delays.append(d)

        monkeypatch.setattr(_utils.asyncio, "sleep", _capture_sleep)

        async def fn():
            raise ValueError("esueaza")

        with pytest.raises(ValueError):
            await _utils.retry_async(
                fn, attempts=5, base_delay=2.0, max_delay=5.0, retry_on=lambda e: True
            )
        # 4 reincercari => 4 sleep-uri: 2, 4, plafonat la 5, plafonat la 5
        assert delays == [2.0, 4.0, 5.0, 5.0]
