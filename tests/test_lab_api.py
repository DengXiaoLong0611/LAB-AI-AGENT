import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from test_lab_components import example_spec

from labagent.api import create_app


class ApiTests(unittest.TestCase):
    def test_web_task_and_download(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(Path(directory))) as client:
                self.assertEqual(client.get("/").status_code, 200)
                self.assertEqual(client.get("/web/app.js").status_code, 200)
                response = client.post("/api/tasks", json=example_spec().model_dump())
                self.assertEqual(response.status_code, 201, response.text)
                task_id = response.json()["id"]
                for _ in range(100):
                    task = client.get("/api/tasks/" + task_id).json()
                    if task["status"] in {"succeeded", "failed", "exhausted"}:
                        break
                    time.sleep(0.03)
                self.assertEqual(task["status"], "succeeded", task.get("error"))
                self.assertTrue(client.get(f"/api/tasks/{task_id}/events").json())
                self.assertEqual(
                    client.get(f"/api/tasks/{task_id}/download").status_code, 200
                )
                self.assertEqual(
                    client.get(f"/api/tasks/{task_id}/logs").status_code, 200
                )
                self.assertEqual(client.get("/api/tasks/missing").status_code, 404)
                artifact = task["state"]["analysis"]["plot_ref"]
                self.assertEqual(
                    client.get(
                        f"/api/tasks/{task_id}/artifacts/{artifact}"
                    ).status_code,
                    200,
                )
                self.assertEqual(
                    client.get("/api/tools").json()["groups"]["devices"][0],
                    "set_fan_field",
                )

    def test_invalid_spec_and_debug(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(Path(directory))) as client:
                data = example_spec().model_dump()
                data["initial_spec"] = [{"type": "uniform", "value": 1000}]
                self.assertEqual(client.post("/api/tasks", json=data).status_code, 422)
                response = client.post(
                    "/api/debug",
                    json={
                        "spec": example_spec().model_dump(),
                        "tool": "set_fan_field",
                        "arguments": {"spec": [{"type": "uniform", "value": 2}]},
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
