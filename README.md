# Agent Articole

Agent automat de cautare articole stiintifice cu interfata HTML de administrare.

## Functionalitati

- **Cautare articole** pe surse academice (arXiv, PubMed, Nature, IEEE, etc.)
- **Filtrare stricta dupa data** — doar articolele din ultimele N zile
- **3 provideri de cautare:** Anthropic Claude, Tavily, Ollama+Tavily (local)
- **Scheduler automat** — ruleaza cautarile la interval configurabil
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

Deschide browser la: **http://localhost:8000**

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
│   ├── routers/
│   │   ├── users.py         # CRUD utilizatori
│   │   ├── topics.py        # CRUD topicuri + gestionare utilizatori
│   │   ├── searches.py      # Cautari manuale + rezultate + istoric
│   │   └── settings_router.py  # Setari API keys
│   ├── services/
│   │   ├── search_anthropic.py  # Claude + web_search tool
│   │   ├── search_tavily.py     # Tavily direct
│   │   ├── search_ollama.py     # Ollama + Tavily ca tool
│   │   └── email_service.py     # Rapoarte email HTML
│   └── templates/
│       └── index.html       # Interfata HTML (Bootstrap 5)
├── static/
│   ├── css/style.css
│   └── js/app.js
├── docs/
│   └── api_examples.md      # Exemple curl + Python
├── .env.example
├── requirements.txt
└── run.py
```

## Provideri suportati

| Provider | Cost | Internet | Local |
|----------|------|----------|-------|
| Anthropic | ~$0.01-0.05/run | Da | Nu |
| Tavily | ~$0.001/run | Da | Nu |
| Ollama+Tavily | Gratuit LLM + Tavily | Da | Da (LLM) |

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
ExecStart=/home/ubuntu/claude/agent_articole/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8007 --log-level info
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

Vezi `docs/api_examples.md` sau Swagger la `http://localhost:8000/docs`.

## Variabile de mediu

| Variabila | Descriere | Default |
|-----------|-----------|---------|
| `ANTHROPIC_API_KEY` | Cheie API Anthropic | — |
| `ANTHROPIC_MODEL` | Modelul Claude | `claude-sonnet-4-6` |
| `TAVILY_API_KEY` | Cheie API Tavily | — |
| `OLLAMA_BASE_URL` | URL Ollama local | `http://localhost:11434` |
| `OLLAMA_MODEL` | Modelul Ollama | `llama3.2` |
| `SMTP_HOST` | Server SMTP | `smtp.gmail.com` |
| `SMTP_PORT` | Port SMTP | `587` |
| `SMTP_USER` | User SMTP | — |
| `SMTP_PASSWORD` | Parola SMTP | — |
| `DATABASE_URL` | URL baza de date | `sqlite:///./agent_articole.db` |
| `DEBUG` | Mod debug | `false` |
