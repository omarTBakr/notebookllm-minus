"""The smallest possible route: proves the app serves without a lifespan."""


async def test_health_reports_the_application(client):
    response = await client.get("/base/health")

    assert response.status_code == 200
    assert response.json() == {
        "application_name": "notebookllm-minus-test",
        "app_version": "0.0.0-test",
    }
