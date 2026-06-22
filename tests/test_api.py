"""Teste de nivel 2 pentru endpoint-urile API (TestClient + SQLite in-memory).

Verifica contractul HTTP: status codes, validari Pydantic, 404/400/429/422,
relatii topic<->user si fluxul de cautare cu providerul mock-uit (fara retea).
"""
import pytest

from app.routers import searches


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_user(client, email="ana@icsi.ro", name="Ana"):
    r = client.post("/api/users", json={"name": name, "email": email})
    assert r.status_code == 201, r.text
    return r.json()


def _make_topic(client, **overrides):
    payload = {"name": "Senzori", "keywords": "senzori optici", "provider": "anthropic"}
    payload.update(overrides)
    r = client.post("/api/topics", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


# ===========================================================================
# Users
# ===========================================================================
class TestUsers:
    def test_create_returneaza_201_si_corp(self, client):
        r = client.post("/api/users", json={"name": "Ana", "email": "ana@icsi.ro"})
        assert r.status_code == 201
        body = r.json()
        assert body["id"] > 0
        assert body["email"] == "ana@icsi.ro"
        assert body["active"] is True

    def test_email_duplicat_da_400(self, client):
        _make_user(client, email="dup@icsi.ro")
        r = client.post("/api/users", json={"name": "Alt", "email": "dup@icsi.ro"})
        assert r.status_code == 400
        assert "registered" in r.json()["detail"].lower()

    def test_email_invalid_da_422(self, client):
        r = client.post("/api/users", json={"name": "X", "email": "not-an-email"})
        assert r.status_code == 422

    def test_get_inexistent_da_404(self, client):
        assert client.get("/api/users/9999").status_code == 404

    def test_listare_ordonata_dupa_id(self, client):
        _make_user(client, email="a@icsi.ro")
        _make_user(client, email="b@icsi.ro")
        ids = [u["id"] for u in client.get("/api/users").json()]
        assert ids == sorted(ids)

    def test_update_modifica_campuri(self, client):
        u = _make_user(client)
        r = client.put(f"/api/users/{u['id']}", json={"name": "Ana Noua", "active": False})
        assert r.status_code == 200
        assert r.json()["name"] == "Ana Noua"
        assert r.json()["active"] is False

    def test_delete(self, client):
        u = _make_user(client)
        assert client.delete(f"/api/users/{u['id']}").status_code == 200
        assert client.get(f"/api/users/{u['id']}").status_code == 404


# ===========================================================================
# Topics — focus pe validari
# ===========================================================================
class TestTopics:
    def test_create_cu_keywords(self, client):
        t = _make_topic(client)
        assert t["id"] > 0
        assert t["keywords"] == "senzori optici"

    def test_create_cu_user_question_fara_keywords(self, client):
        r = client.post("/api/topics", json={
            "name": "Intrebare", "user_question": "Ce e nou in fotovoltaice?",
        })
        assert r.status_code == 201

    def test_fara_keywords_si_fara_question_da_422(self, client):
        r = client.post("/api/topics", json={"name": "Gol"})
        assert r.status_code == 422

    def test_provider_invalid_da_422(self, client):
        r = client.post("/api/topics", json={
            "name": "X", "keywords": "y", "provider": "google",
        })
        assert r.status_code == 422

    @pytest.mark.parametrize("days", [0, 3651, -5])
    def test_days_back_in_afara_limitelor_da_422(self, client, days):
        r = client.post("/api/topics", json={
            "name": "X", "keywords": "y", "days_back": days,
        })
        assert r.status_code == 422

    def test_periodicity_prea_mica_da_422(self, client):
        r = client.post("/api/topics", json={
            "name": "X", "keywords": "y", "periodicity_hours": 0.1,
        })
        assert r.status_code == 422

    def test_create_cu_subscriberi(self, client):
        u = _make_user(client)
        r = client.post("/api/topics", json={
            "name": "X", "keywords": "y", "user_ids": [u["id"]],
        })
        assert r.status_code == 201
        assert [x["id"] for x in r.json()["users"]] == [u["id"]]

    def test_get_inexistent_da_404(self, client):
        assert client.get("/api/topics/9999").status_code == 404


class TestTopicUserRelation:
    def test_adauga_si_scoate_user(self, client):
        u = _make_user(client)
        t = _make_topic(client)

        r = client.post(f"/api/topics/{t['id']}/users/{u['id']}")
        assert r.status_code == 200
        assert any(x["id"] == u["id"] for x in r.json()["users"])

        r = client.delete(f"/api/topics/{t['id']}/users/{u['id']}")
        assert r.status_code == 200
        assert all(x["id"] != u["id"] for x in r.json()["users"])

    def test_adauga_user_inexistent_da_404(self, client):
        t = _make_topic(client)
        assert client.post(f"/api/topics/{t['id']}/users/9999").status_code == 404

    def test_adaugare_idempotenta(self, client):
        u = _make_user(client)
        t = _make_topic(client)
        client.post(f"/api/topics/{t['id']}/users/{u['id']}")
        r = client.post(f"/api/topics/{t['id']}/users/{u['id']}")
        # al doilea apel nu trebuie sa dubleze subscriberul
        assert [x["id"] for x in r.json()["users"]] == [u["id"]]


# ===========================================================================
# Searches — flux run + cooldown (provider mock-uit, fara retea)
# ===========================================================================
class TestSearchRun:
    @pytest.fixture
    def fake_provider(self, monkeypatch):
        """Inlocuieste _dispatch_search ca sa NU loveasca un provider real."""
        async def _fake(topic, telemetry):
            telemetry["api_calls"] = 1
            return [{
                "title": "Articol Test", "url": "https://arxiv.org/abs/1",
                "authors": "Autor X", "source": "arXiv",
                "published_date": "2024-01-01", "summary": "rezumat",
            }]
        monkeypatch.setattr(searches, "_dispatch_search", _fake)

    def test_run_salveaza_rezultatele(self, client, fake_provider):
        t = _make_topic(client)
        r = client.post(f"/api/searches/run/{t['id']}")
        assert r.status_code == 200, r.text
        run = r.json()
        assert run["status"] == "success"
        assert run["results_count"] == 1
        assert run["results"][0]["title"] == "Articol Test"
        assert run["api_calls"] == 1

    def test_al_doilea_run_imediat_da_429_cooldown(self, client, fake_provider):
        t = _make_topic(client)
        assert client.post(f"/api/searches/run/{t['id']}").status_code == 200
        r = client.post(f"/api/searches/run/{t['id']}")
        assert r.status_code == 429
        assert "cooldown" in r.json()["detail"].lower()

    def test_cooldown_persista_intre_restarturi(self, client, db_engine, fake_provider):
        """Cooldown-ul e citit din Topic.last_triggered_at (persistat), nu dintr-o
        stare in-memory: o valoare recenta in DB — ca dupa un restart — blocheaza
        trigger-ul, fara sa fi rulat vreun trigger in acest proces."""
        from datetime import datetime
        from sqlalchemy.orm import sessionmaker
        from app import models

        t = _make_topic(client)
        Session = sessionmaker(bind=db_engine)
        s = Session()
        s.get(models.Topic, t["id"]).last_triggered_at = datetime.now()
        s.commit()
        s.close()

        r = client.post(f"/api/searches/run/{t['id']}")
        assert r.status_code == 429
        assert "cooldown" in r.json()["detail"].lower()

    def test_run_pe_topic_inexistent_da_404(self, client, fake_provider):
        assert client.post("/api/searches/run/9999").status_code == 404

    def test_listare_si_get_run(self, client, fake_provider):
        t = _make_topic(client)
        run_id = client.post(f"/api/searches/run/{t['id']}").json()["id"]

        runs = client.get("/api/searches/runs").json()
        assert any(x["id"] == run_id for x in runs)
        assert client.get(f"/api/searches/runs/{run_id}").status_code == 200

    def test_get_run_inexistent_da_404(self, client):
        assert client.get("/api/searches/runs/9999").status_code == 404

    def test_delete_run(self, client, fake_provider):
        t = _make_topic(client)
        run_id = client.post(f"/api/searches/run/{t['id']}").json()["id"]
        assert client.delete(f"/api/searches/runs/{run_id}").status_code == 200
        assert client.get(f"/api/searches/runs/{run_id}").status_code == 404

    def test_filtrare_results_dupa_topic(self, client, fake_provider):
        t = _make_topic(client)
        client.post(f"/api/searches/run/{t['id']}")
        results = client.get(f"/api/searches/results?topic_id={t['id']}").json()
        assert len(results) == 1
        assert results[0]["topic_id"] == t["id"]


# ===========================================================================
# Endpoint-uri simple
# ===========================================================================
class TestMisc:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_status(self, client):
        u = _make_user(client)
        t = _make_topic(client)
        client.post(f"/api/topics/{t['id']}/users/{u['id']}")
        body = client.get("/api/status").json()
        assert body["active_topics"] >= 1
        assert "version" in body


# ===========================================================================
# Metrics (observabilitate per provider)
# ===========================================================================
class TestMetrics:
    def _seed_runs(self, client, db_engine):
        from datetime import datetime, timedelta
        from sqlalchemy.orm import sessionmaker
        from app import models

        t = _make_topic(client)
        Session = sessionmaker(bind=db_engine)
        s = Session()
        base = datetime(2026, 1, 1, 12, 0, 0)
        s.add_all([
            # anthropic: 1 success (10s, 5 rez), 1 error
            models.SearchRun(topic_id=t["id"], provider="anthropic", status="success",
                             results_count=5, started_at=base, finished_at=base + timedelta(seconds=10),
                             tokens_input=100, tokens_output=200, estimated_cost_usd=0.5),
            models.SearchRun(topic_id=t["id"], provider="anthropic", status="error",
                             results_count=0, started_at=base, finished_at=base + timedelta(seconds=2)),
            # tavily: 1 interrupted (fara finished -> nu intra in durata)
            models.SearchRun(topic_id=t["id"], provider="tavily", status="interrupted",
                             results_count=0, started_at=base),
        ])
        s.commit()
        s.close()
        return t

    def test_metrics_agrega_per_provider_si_total(self, client, db_engine):
        self._seed_runs(client, db_engine)
        body = client.get("/api/metrics").json()

        prov = body["providers"]
        assert prov["anthropic"]["runs"] == 2
        assert prov["anthropic"]["success"] == 1
        assert prov["anthropic"]["error"] == 1
        assert prov["anthropic"]["total_results"] == 5
        assert prov["anthropic"]["success_rate"] == 0.5
        # durata medie peste cele 2 rulari cu finished_at: (10 + 2) / 2
        assert prov["anthropic"]["avg_duration_s"] == 6.0
        assert prov["anthropic"]["estimated_cost_usd"] == 0.5

        assert prov["tavily"]["interrupted"] == 1
        # nicio rulare tavily cu finished_at -> avg None
        assert prov["tavily"]["avg_duration_s"] is None

        totals = body["totals"]
        assert totals["runs"] == 3
        assert totals["success"] == 1
        assert totals["total_results"] == 5

    def test_metrics_gol_intoarce_structura_valida(self, client):
        body = client.get("/api/metrics").json()
        assert body["providers"] == {}
        assert body["totals"]["runs"] == 0
        assert body["totals"]["success_rate"] is None
        assert body["totals"]["avg_duration_s"] is None
