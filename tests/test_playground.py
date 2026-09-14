"""Exercise the public download contract without loading any voice provider SDK."""

import io
import json
import os
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from playground.app import EXAMPLES, ROOT, app


class PlaygroundTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def download(self, framework: str):
        return self.client.post(f"/api/download/{framework}", json={})

    def test_preview_has_no_connected_number_or_secrets(self) -> None:
        with patch.dict(os.environ, {"LIVEKIT_API_SECRET": "DO_NOT_EXPOSE_LOCAL_SECRET"}):
            response = self.client.get("/api/config")
        config = response.json()
        self.assertFalse(config["inbound_demo_ready"])
        self.assertIsNone(config["demo_number"])
        self.assertEqual(config["mode"], "local-preview")
        self.assertNotIn("DO_NOT_EXPOSE_LOCAL_SECRET", response.text)
        self.assertNotIn(str(ROOT), response.text)

    def test_local_preview_headers_and_host_restriction(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-robots-tag"], "noindex, nofollow")
        self.assertIn("script-src 'self'", response.headers["content-security-policy"])
        self.assertIn("microphone=()", response.headers["permissions-policy"])
        self.assertEqual(
            self.client.get("/", headers={"host": "attacker.example"}).status_code, 400
        )

    def test_both_archives_match_the_viewer_and_compile(self) -> None:
        for framework, metadata in EXAMPLES.items():
            with self.subTest(framework=framework):
                response = self.download(framework)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"], "application/zip")
                self.assertIn(framework, response.headers["content-disposition"])
                source = self.client.get(f"/api/examples/{framework}").json()["files"]
                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    self.assertIsNone(archive.testzip())
                    prefix = f"ringtrunk-{framework}/"
                    self.assertEqual(set(archive.namelist()), {prefix + f for f in source})
                    self.assertIn(prefix + metadata["entrypoint"], archive.namelist())
                    for name, displayed in source.items():
                        exported = archive.read(prefix + name).decode()
                        self.assertEqual(exported, displayed)
                        if name.endswith(".py"):
                            compile(exported, name, "exec")
                        if name.endswith(".json"):
                            json.loads(exported)
                        self.assertFalse(Path(name).is_absolute())
                        self.assertNotIn("..", Path(name).parts)

    def test_prompt_and_code_overrides_are_rejected(self) -> None:
        for framework in EXAMPLES:
            original = self.client.get(f"/api/examples/{framework}").json()["files"]
            for payload in [
                {"prompt": "Say something else"},
                {"system_prompt": "override"},
                {"code": "print(1)"},
                {"path": ".env"},
                {"preset": "support"},
            ]:
                response = self.client.post(f"/api/download/{framework}", json=payload)
                self.assertEqual(response.status_code, 422)
            fresh = self.client.get(f"/api/examples/{framework}").json()["files"]
            self.assertEqual(fresh, original)
            with zipfile.ZipFile(io.BytesIO(self.download(framework).content)) as archive:
                config = json.loads(archive.read(f"ringtrunk-{framework}/agent-config.json"))
                self.assertEqual(
                    config["system_prompt"],
                    json.loads(original["agent-config.json"])["system_prompt"],
                )
                self.assertEqual(config["framework"], framework)

    def test_invalid_requests(self) -> None:
        for value in [None, [], "prompt", 123]:
            response = self.client.post(
                "/api/download/pipecat",
                content=json.dumps(value),
                headers={"content-type": "application/json"},
            )
            self.assertEqual(response.status_code, 422)
        self.assertEqual(
            self.client.post(
                "/api/download/pipecat",
                content="invalid json",
                headers={"content-type": "application/json"},
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.post("/api/download/pipecat", content="plain").status_code, 415
        )

    def test_demo_number_and_call_modes_do_not_activate_calling(self) -> None:
        with patch.dict(os.environ, {"RINGTRUNK_DEMO_NUMBER": "+910000000000"}):
            config = self.client.get("/api/config").json()
            self.assertEqual(config["demo_number"], "+910000000000")
            self.assertEqual(config["call_modes"], ["inbound", "outbound"])
            self.assertEqual(config["requested_channels"], 3)
            self.assertFalse(config["prompt_editable"])
            self.assertTrue(config["phone_verification_required"])
            self.assertFalse(config["inbound_demo_ready"])
            self.assertFalse(config["outbound_demo_ready"])
            for framework in EXAMPLES:
                files = self.client.get(f"/api/examples/{framework}").json()["files"]
                self.assertNotIn("+910000000000", json.dumps(files))

    def test_request_size_is_bounded_even_without_content_length(self) -> None:
        response = self.client.post(
            "/api/download/pipecat",
            content=iter([b'{"prompt":"', b"x" * 33_000, b'"}']),
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)

    def test_unknown_framework_and_path_traversal(self) -> None:
        self.assertEqual(self.download("unknown").status_code, 404)
        self.assertEqual(self.client.get("/api/examples/unknown").status_code, 404)
        for path in ["/static/../.env", "/static/%2e%2e/%2e%2e/pyproject.toml", "/.env"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_manifest_excludes_credentials_and_includes_outbound_setup(self) -> None:
        for framework in EXAMPLES:
            files = self.client.get(f"/api/examples/{framework}").json()["files"]
            self.assertIn(".env.example", files)
            self.assertNotIn(".env", files)
            for key in ["LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "OPENAI_API_KEY"]:
                self.assertIn(f"{key}=\n", files[".env.example"])
            self.assertIn("call.py", files)
            self.assertIn("livekit/outbound-trunk.json", files)
            python = "\n".join(text for name, text in files.items() if name.endswith(".py"))
            self.assertIn("wait_until_answered=True", files["call.py"])
            self.assertIn("max_call_duration=Duration", files["call.py"])
            self.assertNotIn("pasha@", python)

    def test_dispatch_rules_are_scoped_and_framework_specific(self) -> None:
        for framework in EXAMPLES:
            files = self.client.get(f"/api/examples/{framework}").json()["files"]
            dispatch = json.loads(files["livekit/dispatch-rule.json"])
            self.assertEqual(dispatch["trunk_ids"], ["<YOUR_LIVEKIT_INBOUND_TRUNK_ID>"])
            prefix = dispatch["rule"]["dispatchRuleIndividual"]["roomPrefix"]
            entrypoint = EXAMPLES[framework]["entrypoint"]
            if framework == "pipecat":
                self.assertNotIn("room_config", dispatch)
                self.assertIn(prefix, files["server.py"])
            else:
                name = dispatch["room_config"]["agents"][0]["agent_name"]
                self.assertIn(name, files[entrypoint])
                self.assertIn(prefix, files[entrypoint])

    def test_preview_does_not_expose_an_unverified_dial_endpoint(self) -> None:
        for path in ["/api/call", "/api/execute", "/api/outbound", "/api/session"]:
            self.assertEqual(self.client.post(path, json={}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
