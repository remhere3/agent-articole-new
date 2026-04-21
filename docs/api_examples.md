# Agent Articole — Documentatie completa API

**URL de baza:** `http://localhost:8006`  
**Swagger interactiv:** `http://localhost:8006/docs`  
**Interfata HTML:** `http://localhost:8006/`

---

## Sumar endpoint-uri

| Metoda | URL | Descriere |
|--------|-----|-----------|
| GET | `/health` | Status server |
| **Utilizatori** | | |
| GET | `/api/users` | Lista toti utilizatorii |
| POST | `/api/users` | Creeaza utilizator |
| GET | `/api/users/{id}` | Detalii utilizator |
| PUT | `/api/users/{id}` | Actualizeaza utilizator |
| DELETE | `/api/users/{id}` | Sterge utilizator |
| **Topicuri** | | |
| GET | `/api/topics` | Lista toate topicurile |
| POST | `/api/topics` | Creeaza topic |
| GET | `/api/topics/{id}` | Detalii topic |
| PUT | `/api/topics/{id}` | Actualizeaza topic |
| DELETE | `/api/topics/{id}` | Sterge topic |
| POST | `/api/topics/{id}/users/{uid}` | Aboneaza utilizator la topic |
| DELETE | `/api/topics/{id}/users/{uid}` | Dezaboneaza utilizator |
| **Cautari** | | |
| POST | `/api/searches/run/{topic_id}` | Declanseaza manual cautare |
| GET | `/api/searches/runs` | Lista rulari |
| GET | `/api/searches/runs/{run_id}` | Detalii rulare + rezultate |
| GET | `/api/searches/results` | Lista articole gasite |
| DELETE | `/api/searches/results/{id}` | Sterge un articol |
| **Setari** | | |
| GET | `/api/settings` | Lista setari curente |
| PUT | `/api/settings/{key}` | Seteaza/actualizeaza o valoare |

---

## 1. Health check

```bash
curl -s http://localhost:8006/health
```
```python
import httpx
r = httpx.get("http://localhost:8006/health")
print(r.json())  # {"status": "ok", "version": "1.0.0"}
```

---

## 2. Utilizatori

### GET /api/users — lista toti utilizatorii

```bash
curl -s http://localhost:8006/api/users | python3 -m json.tool
```
```python
import httpx
r = httpx.get("http://localhost:8006/api/users")
for u in r.json():
    print(f"[{u['id']}] {u['name']} <{u['email']}> activ={u['active']}")
```

---

### POST /api/users — creeaza utilizator

```bash
curl -s -X POST http://localhost:8006/api/users \
  -H "Content-Type: application/json" \
  -d '{"name": "Ion Popescu", "email": "ion@example.com", "active": true}' \
  | python3 -m json.tool
```
```python
r = httpx.post("http://localhost:8006/api/users", json={
    "name": "Ion Popescu",
    "email": "ion@example.com",
    "active": True
})
user = r.json()
print(f"User creat: ID={user['id']}")
```

---

### GET /api/users/{id} — detalii utilizator

```bash
curl -s http://localhost:8006/api/users/1 | python3 -m json.tool
```
```python
r = httpx.get("http://localhost:8006/api/users/1")
print(r.json())
```

---

### PUT /api/users/{id} — actualizeaza utilizator

Toate campurile sunt optionale — trimiti doar ce vrei sa modifici.

```bash
curl -s -X PUT http://localhost:8006/api/users/1 \
  -H "Content-Type: application/json" \
  -d '{"name": "Ion Popescu Jr.", "active": false}'
```
```python
r = httpx.put("http://localhost:8006/api/users/1", json={
    "name": "Ion Popescu Jr.",
    "active": False
})
print(r.json())
```

---

### DELETE /api/users/{id} — sterge utilizator

```bash
curl -s -X DELETE http://localhost:8006/api/users/1
```
```python
r = httpx.delete("http://localhost:8006/api/users/1")
print(r.json())  # {"message": "User 1 deleted"}
```

---

## 3. Topicuri

### GET /api/topics — lista toate topicurile

```bash
curl -s http://localhost:8006/api/topics | python3 -m json.tool
```
```python
r = httpx.get("http://localhost:8006/api/topics")
for t in r.json():
    print(f"[{t['id']}] {t['name']} | {t['provider']} | {t['days_back']}z | activ={t['active']}")
```

---

### POST /api/topics — creeaza topic

**Campuri disponibile:**

| Camp | Tip | Descriere | Default |
|------|-----|-----------|---------|
| `name` | string | Numele topicului | obligatoriu |
| `keywords` | string | Termeni de cautare (fallback) | obligatoriu |
| `user_question` | string | Intrebarea libera adresata agentului | null |
| `days_back` | int | Articole din ultimele N zile | 7 |
| `periodicity_hours` | float | Ruleaza la fiecare N ore | 24 |
| `provider` | string | `anthropic` / `tavily` / `ollama` | `anthropic` |
| `active` | bool | Ruleaza automat | true |
| `send_email` | bool | Trimite email dupa cautare | true |
| `user_ids` | list[int] | Utilizatori abonati | [] |

**Exemplu cu `user_question` (recomandat):**

```bash
curl -s -X POST http://localhost:8006/api/topics \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Terapie Genica",
    "user_question": "Da-mi toate articolele publicate in reviste de specialitate despre terapia genica si CRISPR",
    "keywords": "gene therapy, CRISPR",
    "days_back": 7,
    "periodicity_hours": 24,
    "provider": "anthropic",
    "active": true,
    "send_email": true,
    "user_ids": [1, 2]
  }' | python3 -m json.tool
```
```python
r = httpx.post("http://localhost:8006/api/topics", json={
    "name": "Terapie Genica",
    "user_question": "Da-mi toate articolele publicate in reviste de specialitate despre terapia genica si CRISPR",
    "keywords": "gene therapy, CRISPR",
    "days_back": 7,
    "periodicity_hours": 24,
    "provider": "anthropic",
    "active": True,
    "send_email": True,
    "user_ids": [1, 2]
})
topic = r.json()
print(f"Topic creat: ID={topic['id']}, provider={topic['provider']}")
```

**Exemplu fara `user_question` (cautare automata din keywords):**

```bash
curl -s -X POST http://localhost:8006/api/topics \
  -H "Content-Type: application/json" \
  -d '{
    "name": "AI Climate",
    "keywords": "climate change, machine learning, neural network",
    "days_back": 14,
    "periodicity_hours": 12,
    "provider": "tavily",
    "user_ids": [1]
  }'
```
```python
r = httpx.post("http://localhost:8006/api/topics", json={
    "name": "AI Climate",
    "keywords": "climate change, machine learning, neural network",
    "days_back": 14,
    "periodicity_hours": 12,
    "provider": "tavily",
    "user_ids": [1]
})
print(r.json())
```

---

### GET /api/topics/{id} — detalii topic

```bash
curl -s http://localhost:8006/api/topics/1 | python3 -m json.tool
```
```python
r = httpx.get("http://localhost:8006/api/topics/1")
t = r.json()
print(f"Topic: {t['name']}")
print(f"  Intrebare: {t['user_question']}")
print(f"  Keywords: {t['keywords']}")
print(f"  Ultima rulare: {t['last_run_at']}")
print(f"  Utilizatori: {[u['email'] for u in t['users']]}")
```

---

### PUT /api/topics/{id} — actualizeaza topic

```bash
# Schimba provider-ul si periodicitatea
curl -s -X PUT http://localhost:8006/api/topics/1 \
  -H "Content-Type: application/json" \
  -d '{"provider": "tavily", "periodicity_hours": 6, "days_back": 30}'

# Actualizeaza intrebarea
curl -s -X PUT http://localhost:8006/api/topics/1 \
  -H "Content-Type: application/json" \
  -d '{"user_question": "Arata-mi studii clinice despre imunoterapie in cancer publicate recent"}'

# Dezactiveaza topicul
curl -s -X PUT http://localhost:8006/api/topics/1 \
  -H "Content-Type: application/json" \
  -d '{"active": false}'

# Schimba utilizatorii abonati
curl -s -X PUT http://localhost:8006/api/topics/1 \
  -H "Content-Type: application/json" \
  -d '{"user_ids": [1, 3, 5]}'
```
```python
# Actualizeaza mai multe campuri
r = httpx.put("http://localhost:8006/api/topics/1", json={
    "user_question": "Arata-mi studii clinice despre imunoterapie in cancer",
    "days_back": 30,
    "periodicity_hours": 6,
    "provider": "anthropic",
    "user_ids": [1, 3]
})
print(r.json())
```

---

### DELETE /api/topics/{id} — sterge topic

Sterge topicul si toate rezultatele/rularile asociate.

```bash
curl -s -X DELETE http://localhost:8006/api/topics/1
```
```python
r = httpx.delete("http://localhost:8006/api/topics/1")
print(r.json())  # {"message": "Topic 1 deleted"}
```

---

### POST /api/topics/{id}/users/{uid} — aboneaza utilizator

```bash
curl -s -X POST http://localhost:8006/api/topics/1/users/2
```
```python
r = httpx.post("http://localhost:8006/api/topics/1/users/2")
print(r.json())  # TopicOut cu users actualizat
```

---

### DELETE /api/topics/{id}/users/{uid} — dezaboneaza utilizator

```bash
curl -s -X DELETE http://localhost:8006/api/topics/1/users/2
```
```python
r = httpx.delete("http://localhost:8006/api/topics/1/users/2")
print(r.json())
```

---

## 4. Cautari

### POST /api/searches/run/{topic_id} — declanseaza manual

Ruleaza imediat cautarea pentru un topic, indiferent de programul automat.

```bash
# Ruleaza si afiseaza rezultatele
curl -s -X POST http://localhost:8006/api/searches/run/1 | python3 -m json.tool
```
```python
r = httpx.post("http://localhost:8006/api/searches/run/1", timeout=120.0)
run = r.json()
print(f"Run #{run['id']}: {run['status']} — {run['results_count']} articole ({run['provider']})")
if run['error_message']:
    print(f"Eroare: {run['error_message']}")
for a in run['results']:
    print(f"\n  {a['title']}")
    print(f"  URL: {a['url']}")
    print(f"  Publicat: {a['published_date']} | Sursa: {a['source']}")
    if a['summary']:
        print(f"  Rezumat: {a['summary'][:120]}...")
```

---

### GET /api/searches/runs — lista rulari

```bash
# Toate rularile (ultimele 50)
curl -s "http://localhost:8006/api/searches/runs" | python3 -m json.tool

# Rularile unui topic specific
curl -s "http://localhost:8006/api/searches/runs?topic_id=1"

# Ultimele 10 rulari
curl -s "http://localhost:8006/api/searches/runs?limit=10"

# Combinate
curl -s "http://localhost:8006/api/searches/runs?topic_id=1&limit=5"
```
```python
# Toate rularile unui topic
r = httpx.get("http://localhost:8006/api/searches/runs", params={
    "topic_id": 1,
    "limit": 20
})
for run in r.json():
    print(f"Run #{run['id']}: {run['status']} | {run['results_count']} articole | {run['started_at']}")
```

---

### GET /api/searches/runs/{run_id} — detalii rulare completa

Returneaza rularea cu toate articolele gasite in acea rulare.

```bash
curl -s http://localhost:8006/api/searches/runs/5 | python3 -m json.tool
```
```python
r = httpx.get("http://localhost:8006/api/searches/runs/5")
run = r.json()
print(f"Run #{run['id']}: {run['status']} | {run['results_count']} articole")
print(f"  Inceput: {run['started_at']}")
print(f"  Terminat: {run['finished_at']}")
for a in run['results']:
    print(f"  - {a['title']}")
```

---

### GET /api/searches/results — lista articole gasite

```bash
# Toate articolele (ultimele 100)
curl -s "http://localhost:8006/api/searches/results" | python3 -m json.tool

# Filtrat dupa topic
curl -s "http://localhost:8006/api/searches/results?topic_id=1"

# Cu limita custom
curl -s "http://localhost:8006/api/searches/results?topic_id=1&limit=20"
```
```python
r = httpx.get("http://localhost:8006/api/searches/results", params={
    "topic_id": 1,
    "limit": 50
})
for a in r.json():
    print(f"[{a['published_date']}] {a['title']}")
    print(f"  {a['url']}")
    print(f"  {a['source']} | {a['provider']}")
```

---

### DELETE /api/searches/results/{id} — sterge articol

```bash
curl -s -X DELETE http://localhost:8006/api/searches/results/42
```
```python
r = httpx.delete("http://localhost:8006/api/searches/results/42")
print(r.json())  # {"message": "Result 42 deleted"}
```

---

## 5. Setari

### GET /api/settings — lista setari

Valorile sensibile (API keys, parole) sunt mascate partial.

```bash
curl -s http://localhost:8006/api/settings | python3 -m json.tool
```
```python
r = httpx.get("http://localhost:8006/api/settings")
for s in r.json():
    print(f"{s['key']}: {s['value']}")
```

---

### PUT /api/settings/{key} — seteaza o valoare

Chei disponibile: `anthropic_api_key`, `anthropic_model`, `tavily_api_key`,
`ollama_base_url`, `ollama_model`, `smtp_host`, `smtp_port`, `smtp_user`,
`smtp_password`, `email_from`.

```bash
# API key Anthropic
curl -s -X PUT http://localhost:8006/api/settings/anthropic_api_key \
  -H "Content-Type: application/json" \
  -d '{"key": "anthropic_api_key", "value": "sk-ant-..."}'

# Model Anthropic
curl -s -X PUT http://localhost:8006/api/settings/anthropic_model \
  -H "Content-Type: application/json" \
  -d '{"key": "anthropic_model", "value": "claude-opus-4-7"}'

# API key Tavily
curl -s -X PUT http://localhost:8006/api/settings/tavily_api_key \
  -H "Content-Type: application/json" \
  -d '{"key": "tavily_api_key", "value": "tvly-..."}'

# Ollama URL si model
curl -s -X PUT http://localhost:8006/api/settings/ollama_base_url \
  -H "Content-Type: application/json" \
  -d '{"key": "ollama_base_url", "value": "http://localhost:11434"}'

curl -s -X PUT http://localhost:8006/api/settings/ollama_model \
  -H "Content-Type: application/json" \
  -d '{"key": "ollama_model", "value": "llama3.2"}'

# Email SMTP
curl -s -X PUT http://localhost:8006/api/settings/smtp_host \
  -H "Content-Type: application/json" \
  -d '{"key": "smtp_host", "value": "smtp.gmail.com"}'

curl -s -X PUT http://localhost:8006/api/settings/smtp_user \
  -H "Content-Type: application/json" \
  -d '{"key": "smtp_user", "value": "you@gmail.com"}'

curl -s -X PUT http://localhost:8006/api/settings/smtp_password \
  -H "Content-Type: application/json" \
  -d '{"key": "smtp_password", "value": "app_password_here"}'
```
```python
BASE = "http://localhost:8006/api"

def set_setting(key: str, value: str):
    r = httpx.put(f"{BASE}/settings/{key}", json={"key": key, "value": value})
    r.raise_for_status()
    print(f"  {key} = salvat")

# Configurare initiala completa
set_setting("anthropic_api_key", "sk-ant-...")
set_setting("anthropic_model",   "claude-sonnet-4-6")
set_setting("tavily_api_key",    "tvly-...")
set_setting("ollama_base_url",   "http://localhost:11434")
set_setting("ollama_model",      "llama3.2")
set_setting("smtp_host",         "smtp.gmail.com")
set_setting("smtp_port",         "587")
set_setting("smtp_user",         "you@gmail.com")
set_setting("smtp_password",     "app_password")
set_setting("email_from",        "Agent Articole <you@gmail.com>")
```

---

## 6. Exemplu complet end-to-end

```python
import httpx

BASE = "http://localhost:8006/api"
client = httpx.Client(timeout=120.0)

# 1. Configureaza cheile API
client.put(f"{BASE}/settings/anthropic_api_key",
           json={"key": "anthropic_api_key", "value": "sk-ant-..."})

# 2. Creeaza utilizatori
u1 = client.post(f"{BASE}/users",
                 json={"name": "Ana Ionescu", "email": "ana@research.ro"}).json()
u2 = client.post(f"{BASE}/users",
                 json={"name": "Mihai Pop", "email": "mihai@research.ro"}).json()
print(f"Utilizatori: {u1['id']}, {u2['id']}")

# 3. Creeaza topic cu intrebare libera
topic = client.post(f"{BASE}/topics", json={
    "name": "Oncologie 2026",
    "user_question": (
        "Da-mi toate articolele publicate in reviste de specialitate "
        "despre imunoterapie si terapii tinta in cancer"
    ),
    "keywords": "immunotherapy, targeted therapy, cancer",
    "days_back": 7,
    "periodicity_hours": 24,
    "provider": "anthropic",
    "send_email": True,
    "user_ids": [u1["id"], u2["id"]]
}).json()
print(f"Topic: #{topic['id']} — {topic['name']}")

# 4. Declanseaza cautarea manual
print("Caut articole...")
run = client.post(f"{BASE}/searches/run/{topic['id']}").json()
print(f"Rezultat: {run['status']} — {run['results_count']} articole in {run['provider']}")

# 5. Afiseaza articolele
results = client.get(f"{BASE}/searches/results",
                     params={"topic_id": topic["id"]}).json()
for i, a in enumerate(results, 1):
    print(f"\n{i}. {a['title']}")
    print(f"   {a['url']}")
    print(f"   {a['source']} | {a['published_date']}")
    if a["summary"]:
        print(f"   {a['summary'][:150]}...")
```

---

## 7. Raspunsuri de eroare

Toate erorile returneaza JSON cu campul `detail`:

```json
{"detail": "Topic not found"}
{"detail": "Email already registered"}
{"detail": "ANTHROPIC_API_KEY not configured"}
{"detail": "provider must be one of {'anthropic', 'tavily', 'ollama'}"}
```

```python
r = httpx.post(f"{BASE}/searches/run/999")
if r.status_code != 200:
    print(f"Eroare {r.status_code}: {r.json()['detail']}")
```
