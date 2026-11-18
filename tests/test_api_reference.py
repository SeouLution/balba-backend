from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_scalar_loads_the_repository_spec():
    reference = client.get("/scalar")
    spec = client.get("/openapi.yaml")
    expected_spec = Path(__file__).resolve().parents[1] / "openapi.yaml"

    assert reference.status_code == 200
    assert "@scalar/api-reference" in reference.text
    assert "/openapi.yaml" in reference.text
    assert spec.status_code == 200
    assert spec.headers["content-type"] == "application/yaml"
    assert spec.content == expected_spec.read_bytes()
