# Agent Articole

Agent automat de cautare articole stiintifice cu interfata HTML de administrare.

## Functionalitati

- **Cautare articole** pe surse academice (arXiv, PubMed, Nature, IEEE, etc.)
- **Filtrare stricta dupa data** — parametru nativ API + filtru garantat Python post-procesare
- **4 provideri de cautare:** Anthropic Claude, Tavily, SearXNG+Ollama (self-hosted), Author (OpenAlex+CrossRef)
- **Stergere watermarks IEEE** — snippet-urile cu text institutional IEEE sunt curatate automat
- **Reincercari automate** — apelurile externe (Tavily/Ollama/SearXNG) se reincearca cu backoff exponential la erori tranzitorii
- **Validare input** — limite de lungime pe campurile care intra in prompt (anti prompt-injection / overflow)
- **Scheduler automat** — ruleaza cautarile la interval configurabil, cu sesiune DB per topic si timeout configurabil
- **Rapoarte email** catre utilizatori abonati dupa fiecare cautare
- **Interfata HTML** de administrare (fara framework frontend)
- **REST API** documentat (Swagger la `/docs`)

## Instalare rapida

```bash
cd /home/mihai/python/proiecte/agent_articole

# Mediu virtual
python3 -m venv venv
source venv/bin/activate

# Dependente
pip install -r requirements.txt

# Configureaza cheile API
cp .env.example .env
nano .env   # seteaza ANTHROPIC_API_KEY si/sau TAVILY_API_KEY

# Porneste serverul
python run.py
```

Deschide browser la: **http://localhost:8002**

## Structura proiect

```
agent_articole/
├── app/
│   ├── main.py              # FastAPI app + lifespan
│   ├── config.py            # Setari din .env
│   ├── database.py          # SQLAlchemy + SQLite
│   ├── models.py            # Modele BD: User, Topic, SearchResult, SearchRun
│   ├── schemas.py           # Pydantic schemas
│   ├── scheduler.py         # APScheduler — orchestrare periodica
│   ├── log_stream.py        # SSE log stream live catre browser
│   ├── routers/
│   │   ├── users.py         # CRUD utilizatori
│   │   ├── topics.py        # CRUD topicuri + gestionare utilizatori
│   │   └── searches.py      # Cautari manuale + rezultate + istoric
│   ├── services/
│   │   ├── _utils.py            # Utilitare comune: parse_date, strip_watermarks, retry_async, domenii academice
│   │   ├── search_anthropic.py  # Claude + web_search tool
│   │   ├── search_tavily.py     # Tavily direct
│   │   ├── search_searxng.py    # SearXNG + Ollama (self-hosted)
│   │   ├── search_author.py     # OpenAlex + CrossRef (cautare dupa autor)
│   │   ├── email_service.py     # Rapoarte email HTML
│   │   └── ntfy_service.py      # Notificari push ntfy.sh
│   └── templates/
│       ├── index.html          # Interfata HTML (Bootstrap 5)
│       └── documentation.html  # Documentatie completa + diagrame
├── static/
│   ├── css/style.css
│   └── js/app.js
├── config/
│   └── academic_domains.txt # Lista domenii academice (editabila fara cod)
├── docs/
│   └── api_examples.md      # Exemple curl + Python
├── tests/
│   ├── conftest.py          # Fixturi: TestClient + SQLite in-memory, override get_db
│   ├── test_utils.py        # Nivel 1 — teste unitare pentru _utils.py
│   ├── test_api.py          # Nivel 2 — endpoint-uri REST (TestClient)
│   └── test_providers.py    # Nivel 2 — provider author cu respx (mock HTTP)
├── pytest.ini               # Config pytest (asyncio_mode=strict)
├── .env.example
├── requirements.txt
├── requirements-dev.txt     # Dependinte de test (pytest, pytest-asyncio, respx)
└── run.py
```

## Provideri suportati

| Provider | Cost | Internet | Local | Necesita |
|----------|------|----------|-------|----------|
| Anthropic | ~$0.01-0.05/run | Da | Nu | `ANTHROPIC_API_KEY` |
| Tavily | ~$0.001/run | Da | Nu | `TAVILY_API_KEY` |
| SearXNG+Ollama | 0 | Self-hosted | Da | `SEARXNG_BASE_URL` + Ollama |
| Author (OpenAlex+CrossRef) | 0 | Da | Nu | nimic (fara API key) |

### De ce `anthropic` si `author` intorc numere diferite de rezultate

Pentru acelasi topic (mai ales cand e un **nume de autor**), providerul `anthropic`
intoarce de regula **mai putine** articole decat `author`, iar seturile pot diferi de
la o rulare la alta. Nu este un bug — sunt **doua mecanisme fundamental diferite**:

| | `anthropic` | `author` |
|---|---|---|
| Sursa | Web search generic via Claude (`web_search` tool) | OpenAlex + CrossRef (baze bibliografice) |
| Interpretare query | tratat ca **topic**, termenii descriptivi tradusi in EN | tratat ca **entitate-autor** (rezolvat la author ID) |
| Acoperire | esantion "best effort" din ce iese la cateva cautari web | lista cvasi-completa a productiei autorului |
| Limita | nr. de cautari pe care le decide modelul + buget de tokeni | `AUTHOR_MAX_WORKS` lucrari/profil × `AUTHOR_MAX_PROFILES` profiluri (OpenAlex + CrossRef paginate cu cursor) |
| Determinism | **non-determinist** — rezultatele variaza intre rulari | determinist — aceeasi interogare → aceleasi date |
| Abstract | sintetizat de model | abstract real din baza de date |

**De ce `anthropic` gaseste mai putin:**

1. **E o cautare web, nu o interogare de catalog.** Claude ruleaza cateva cautari pe
   motoare generale (arxiv, pubmed, scholar, nature) si raporteaza ce gaseste si
   *decide sa listeze*. Nu parcurge bibliografia completa a unui autor.
2. **Se opreste devreme din proprie initiativa.** Chiar daca `max_uses` permite mai
   multe cautari, modelul tinde sa se opreasca dupa cateva — deci marirea bugetului de
   cautari **nu** creste neaparat acoperirea.
3. **Numele proprii sunt fragile pe web.** Promptul `anthropic` **nu traduce numele
   proprii** in engleza (doar termenii descriptivi ai topicului) — un nume tradus sau
   transliterat devine alta interogare si rateaza autorul real. In plus, **diacriticele
   romanesti** (`ș î ă ț â`) sunt problematice: multe indexuri stocheaza forma *fara*
   diacritice (ex. `Ștefanescu` indexat ca `Stefanescu`), altele o pastreaza. De aceea
   promptul cere variante de nume (cu/fara diacritice, ordine diferita, forma cu
   initiala) — acoperirea creste, dar ramane partiala fata de `author`.
4. **Non-determinism intrinsec.** Web search-ul returneaza altceva la fiecare rulare,
   deci numarul si setul de articole fluctueaza (ex. 5 intr-o rulare, 4 in urmatoarea,
   cu titluri partial diferite).

**De ce `author` gaseste mai mult:** OpenAlex si CrossRef **indexeaza direct productia
autorului**. Pasul 1 rezolva numele la un author ID, pasul 2 descarca toate lucrarile
din fereastra de timp (`days_back`), apoi cele doua surse se fuzioneaza si se dedupica
dupa titlu. Pentru intrebarea "tot ce a publicat persoana X", aceasta este ruta corecta.

**Exemplu real** (topic "articole Nicolae Georgescu", `days_back=3650`):

- `author` → **12 articole** (lista completa, cu abstracte)
- `anthropic` → **5 articole**, toate fiind un **subset** al celor 12 (intr-o alta
  rulare: 4 articole, set partial diferit, incluzand 2 publicatii locale / de
  conferinta care *nu* apar in OpenAlex/CrossRef)

Filtrarea pe data **nu** este cauza diferentei — ambele provideri folosesc aceeasi
fereastra `days_back`; diferenta vine pur din acoperirea sursei.

**Recomandare de utilizare:**

- Nume de persoana / "toate articolele autorului X" → foloseste `author` (complet, gratuit, determinist).
- Topic tematic ("articole recente despre fuziune nucleara") → `anthropic` (web search-ul exceleaza la teme, nu la enumerarea bibliografiei unui autor).

## Deployment ca serviciu systemd

### Creare serviciu

```bash
sudo nano /etc/systemd/system/agent-articole.service
```

```ini
[Unit]
Description=Agent Articole FastAPI
After=network.target

[Service]
Type=exec
User=ubuntu
WorkingDirectory=/home/ubuntu/claude/agent_articole
ExecStart=/home/ubuntu/claude/agent_articole/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8002 --log-level info
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl start agent-articole
sudo systemctl enable agent-articole
```

### De ce un singur worker

Aplicatia foloseste **APScheduler** (pornit in `lifespan`) si **SSE log streaming** (stare in memorie). Cu mai multi workers:

- Log stream-ul SSE din UI afiseaza doar log-urile unui singur worker

Un singur process async (FastAPI + AsyncIOScheduler) este suficient — concurenta e gestionata de event loop, nu de procese multiple.

**Protectie la rulare:** chiar daca pornesti din greseala mai multi workers pe acelasi host, scheduler-ul foloseste un **flock de singleton** (`scheduler_lock_path`, implicit `/tmp/agent_articole_scheduler.lock`). Doar primul proces care obtine lock-ul porneste joburile periodice; ceilalti ies tacut. Astfel joburile **nu** se mai executa de N ori. (Lock-ul e per-host, ceea ce se potriveste cu SQLite local.)

### Reziliență la servicii externe

Apelurile catre API-urile externe au doua straturi de protectie:

- **Retry cu backoff** (`retry_async` in `app/services/_utils.py`) — reincearca erorile tranzitorii (timeout, conexiune, 429, 5xx). Anthropic foloseste backoff lung (60s, 120s) cu retry-ul intern al SDK-ului dezactivat; ceilalti, backoff exponential scurt.
- **Circuit breaker per-provider** (`app/services/_circuit.py`) — dupa N esecuri *de infrastructura* consecutive, circuitul se DESCHIDE: apelurile urmatoare catre acel provider esueaza instant (fail-fast) fara sa mai loveasca serviciul picat. Dupa cooldown trece in *half-open* (un apel de proba); succes → inchis, esec → redeschis cu cooldown dublat (plafonat). Astfel un API extern jos nu mai e lovit prin tot ciclul de retry la fiecare topic, la fiecare rulare.

  | Provider | Prag esecuri | Cooldown | De ce |
  |----------|:---:|:---:|-------|
  | anthropic | 2 | 300s | fiecare esec = pana la ~180s (retry lung) |
  | tavily | 3 | 180s | platit, apel rapid |
  | searxng | 3 | 120s | local, proba ieftina |
  | author | 4 | 300s | OpenAlex+CrossRef gratuite, mai instabile |

  Un raspuns valid cu 0 rezultate **nu** e esec — doar erorile de infra deschid circuitul. Providerii care isi inghiteau erorile (Tavily/SearXNG/Author) arunca acum `ProviderDownError` cand serviciul e clar indisponibil, ca breaker-ul sa le numere. Starea e in memorie (sigura datorita single-process-ului) si se reseteaza la restart.

### Shutdown gratios

La oprire (deploy, restart, SIGTERM), `mark_interrupted_runs()` (`app/scheduler.py`, apelat in `lifespan` dupa `stop_scheduler`) marcheaza orice rulare ramasa in status `running` ca `interrupted` (cu `finished_at` + mesaj). Altfel o cautare intrerupta la mijloc ar ramane blocata pe veci ca `running` si ar parea activa la repornire. E sigur sa maturam toate rularile `running` fiindca deployment-ul e single-process (SQLite + scheduler singleton).

### Observabilitate — `GET /api/metrics`

Agregari per provider plus un total general: numar de `runs`, defalcare pe status (`success` / `error` / `interrupted` / `running`), `success_rate`, durata medie (`avg_duration_s`), `total_results`, tokeni si `estimated_cost_usd`. Agregarea se face in Python (portabil), potrivit la scara aplicatiei.

### Migrari de schema

Aplicatia **nu** foloseste Alembic, desi e in dependente. Schema evolueaza prin `Base.metadata.create_all` + un pas idempotent `_ensure_columns()` (`app/database.py`) care adauga coloane noi cu `ALTER TABLE ADD COLUMN` daca lipsesc.

Decizie deliberata pentru SQLite single-instance, unde evolutia se reduce la adaugare de coloane. Limitari acceptate constient: nu gestioneaza redenumiri/stergeri de coloane, schimbari de tip/constrangeri, backfill sau downgrade; o modificare a *definitiei* unei coloane existente nu e detectata. Daca migram la Postgres sau apar astfel de nevoi → reintroducem Alembic.

### Comenzi utile

```bash
sudo systemctl status agent-articole    # status
sudo systemctl restart agent-articole   # repornire dupa modificari
sudo journalctl -u agent-articole -f    # log-uri live
```

## Testare

Suita de teste (`pytest`) traieste in `tests/` — **105 teste**, toate ruleaza local,
**fara internet, fara chei API si fara baza de date reala**. E impartita pe doua niveluri.

| Nivel | Fisier | Ce verifica | Izolare |
|-------|--------|-------------|---------|
| **1** | `tests/test_utils.py` | Functiile pure din `app/services/_utils.py` (parsare data, watermark, domenii, potrivire nume, retry) | Niciuna (functii pure) |
| **2** | `tests/test_api.py` | Endpoint-urile REST (CRUD, validari, 404/400/422/429, flux de cautare) | SQLite in-memory + provider mock-uit |
| **2** | `tests/test_providers.py` | Providerul `author` (OpenAlex + CrossRef): parsare, filtrare, deduplicare | HTTP mock-uit cu `respx` |

### 1. Instalare dependinte de test

Dependintele de test sunt separate de cele de productie, in `requirements-dev.txt`:

```bash
source venv/bin/activate
pip install -r requirements-dev.txt
```

### 2. Rulare

```bash
pytest                                  # toata suita (config din pytest.ini)
pytest tests/test_utils.py              # un singur fisier
pytest tests/test_api.py::TestUsers     # o singura clasa
pytest -k cooldown                      # filtreaza dupa nume
pytest -v -x                            # verbose, opreste la primul esec
```

Rezultat asteptat: `105 passed`. Daca `pytest` nu e in PATH (venv neactivat),
ruleaza prin interpretorul din venv: `./venv/bin/python -m pytest`.

### Nivel 1 — teste unitare (`test_utils.py`)

Acopera functiile pure: primesc input, se verifica iesirea. Niciun setup, ruleaza in milisecunde,
si prind regresiile tacite in zonele fragile (regex de watermark, ordinea formatelor de data, logica
de retry). Functii testate: `parse_date`, `strip_watermarks`, `domain` / `is_academic`,
`_load_academic_domains`, `looks_like_person_name`, `author_in_result`, `is_retryable_http`,
`describe_exc`, `retry_async`.

### Nivel 2 — endpoint-uri (`test_api.py`)

Pornesc aplicatia FastAPI in memorie cu `TestClient` si o baza **SQLite in-memory** izolata per test.
Fixtura din `tests/conftest.py` suprascrie dependenta `get_db` (deci nu se atinge `agent_articole.db`
real) si nu declanseaza `lifespan` (deci scheduler-ul real nu porneste). Fluxul de cautare e testat
cu providerul **mock-uit** (`monkeypatch` pe `searches._dispatch_search`) — zero apeluri externe.
Se verifica: CRUD users/topics, validari `422`, email duplicat `400`, resursa inexistenta `404`,
relatia topic↔user, si cooldown-ul de trigger (`429`).

### Nivel 2 — provideri cu `respx` (`test_providers.py`)

`respx` intercepteaza apelurile HTTP catre `api.openalex.org` / `api.crossref.org` si le intoarce un
raspuns fix. Astfel se testeaza **logica noastra** de parsare/filtrare/deduplicare — determinist, fara
internet, fara cost. Cazuri: parsare work OpenAlex (URL DOI, abstract din inverted index), filtrare
dupa `cutoff`, deduplicare titlu intre OA si CR, filtrare autor nepotrivit, si reziliența cand OpenAlex
pica (500) — providerul continua cu CrossRef.

### Cum adaugi teste noi

1. Pune fisierul in `tests/`, cu nume `test_*.py` (altfel `pytest` nu il colecteaza).
2. Pentru endpoint-uri: foloseste fixtura `client` din `conftest.py` (TestClient cu DB izolata, gata facut).
3. Pentru cod `async`: marcheaza testul cu `@pytest.mark.asyncio`.
4. Pentru orice apel HTTP extern: mock-uieste-l cu `respx` — testele NU trebuie sa atinga reteaua.
5. Ruleaza `pytest -v` si verifica ca totul e verde inainte de commit.

## Documentatie API

Vezi `docs/api_examples.md` sau Swagger la `http://localhost:8002/docs`.

## Variabile de mediu

| Variabila | Descriere | Default |
|-----------|-----------|---------|
| `ANTHROPIC_API_KEY` | Cheie API Anthropic | — |
| `ANTHROPIC_MODEL` | Modelul Claude (pretul costului se ia automat dupa model) | `claude-opus-4-8` |
| `TAVILY_API_KEY` | Cheie API Tavily | — |
| `SEARXNG_BASE_URL` | URL server SearXNG self-hosted (necesar pt. provider `searxng`) | `http://localhost:8080` |
| `SEARXNG_MAX_ARTICLES` | Cate articole trimite `searxng` la Ollama (cap per rulare) | `25` |
| `AUTHOR_MAX_WORKS` | Provider `author`: lucrari/profil (OpenAlex + CrossRef, paginate cu cursor) | `200` |
| `AUTHOR_MAX_PROFILES` | Provider `author`: cate profiluri de autor potrivite se proceseaza | `3` |
| `OLLAMA_BASE_URL` | URL Ollama local (folosit de `searxng` pt. rezumare) | `http://localhost:11434` |
| `OLLAMA_MODEL` | Modelul Ollama | `llama3.2` |
| `OLLAMA_API_KEY` | API key Ollama Cloud; gol = local | — |
| `SMTP_HOST` | Server SMTP | `smtp.gmail.com` |
| `SMTP_PORT` | Port SMTP | `587` |
| `SMTP_USER` | User SMTP | — |
| `SMTP_PASSWORD` | Parola SMTP | — |
| `SMTP_TIMEOUT` | Timeout (s) pe operatia SMTP; server blocat nu mai tine jobul ostatic | `30.0` |
| `EMAIL_FROM` | Expeditor afisat in email | `Agent Articole <noreply@example.com>` |
| `APP_SECRET_KEY` | Cheie secreta aplicatie (schimba in productie!) | `dev-secret-change-in-production` |
| `ENFORCE_SECRET_KEY` | Optional. Daca `true`, la pornire cere ca `APP_SECRET_KEY` sa fie schimbata: in productie refuza pornirea, in debug doar avertizeaza. Implicit off (cheia nu e obligatorie) | `false` |
| `APP_PORT` | Portul serverului | `8002` |
| `VERSION` | Versiunea afisata in UI/documentatie | `1.2` |
| `DATABASE_URL` | URL baza de date | `sqlite:///./agent_articole.db` |
| `DEBUG` | Mod debug | `false` |
| `NTFY_ENABLED` | Notificari push ntfy per rulare | `false` |
| `NTFY_BASE_URL` | Server ntfy (`https://ntfy.sh` sau local) | `https://ntfy.sh` |
| `NTFY_TOPIC` | Topicul ntfy pentru notificari | — |
