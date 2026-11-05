from fastapi.testclient import TestClient

from p2p.main import app


def test_health_answers_even_without_database() -> None:
    r = TestClient(app).get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["database"] in ("ok", "unreachable")
