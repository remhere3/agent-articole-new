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
├── .env.example
├── requirements.txt
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
| Limita | nr. de cautari pe care le decide modelul + buget de tokeni | pana la 100 (OpenAlex) / 50 (CrossRef) inregistrari |
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

- Fiecare worker porneste propriul scheduler → joburile se executa de N ori simultan
- Log stream-ul SSE din UI afiseaza doar log-urile unui singur worker

Un singur process async (FastAPI + AsyncIOScheduler) este suficient — concurenta e gestionata de event loop, nu de procese multiple.

### Comenzi utile

```bash
sudo systemctl status agent-articole    # status
sudo systemctl restart agent-articole   # repornire dupa modificari
sudo journalctl -u agent-articole -f    # log-uri live
```

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
| `EMAIL_FROM` | Expeditor afisat in email | `Agent Articole <noreply@example.com>` |
| `APP_SECRET_KEY` | Cheie secreta aplicatie (schimba in productie!) | `dev-secret-change-in-production` |
| `APP_PORT` | Portul serverului | `8002` |
| `VERSION` | Versiunea afisata in UI/documentatie | `1.2` |
| `DATABASE_URL` | URL baza de date | `sqlite:///./agent_articole.db` |
| `DEBUG` | Mod debug | `false` |
| `NTFY_ENABLED` | Notificari push ntfy per rulare | `false` |
| `NTFY_BASE_URL` | Server ntfy (`https://ntfy.sh` sau local) | `https://ntfy.sh` |
| `NTFY_TOPIC` | Topicul ntfy pentru notificari | — |
