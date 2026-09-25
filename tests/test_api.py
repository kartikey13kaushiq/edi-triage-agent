from fastapi.testclient import TestClient

from edi_triage import api


def test_http_flow(service, labelled):
    api.app.dependency_overrides[api.get_service] = lambda: service
    client = TestClient(api.app)
    try:
        created = client.post("/incidents", json=labelled["INC-024"]["incident"])
        assert created.status_code == 201
        body = created.json()
        assert body["status"] == "awaiting_approval"
        thread = body["thread_id"]

        assert client.get(f"/incidents/{thread}").json()["status"] == "awaiting_approval"
        decided = client.post(f"/incidents/{thread}/decision", json={"approved": True, "reviewer": "ops"})
        assert decided.json()["status"] == "approved"

        again = client.post(f"/incidents/{thread}/decision", json={"approved": True, "reviewer": "ops"})
        assert again.status_code == 409
        assert client.get("/incidents/missing").status_code == 404
    finally:
        api.app.dependency_overrides.clear()
