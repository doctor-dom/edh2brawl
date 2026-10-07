from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_status_endpoint():
    res = client.get("/api/status")
    assert res.status_code == 200
    assert "cards_indexed" in res.json()


def test_index_html():
    res = client.get("/")
    assert res.status_code == 200
    assert "edh2brawl" in res.text
