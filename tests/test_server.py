"""Exercise direct browser-generation API with the actual native writer."""

import json
import shutil
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from distribution_drawing.generator import generate, output_size
from distribution_drawing.server import make_server


class LocalAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.server = make_server(0, cls.root / "outputs", cls.root / "cache")
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.token = cls.server.token

    @classmethod
    def tearDownClass(cls):
        cls.server.jobs.close()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and any(
            j["status"] in ("queued", "building", "generating", "flushing", "cancelling")
            for j in cls.server.jobs.jobs.values()
        ):
            time.sleep(0.02)
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temporary.cleanup()

    def request(self, path, data=None, headers=None):
        headers = dict(headers or {})
        if data is not None:
            headers.setdefault("Content-Type", "application/json")
            headers.setdefault("X-Distribution-Token", self.token)
        request = Request(
            self.url + path,
            data=None if data is None else json.dumps(data).encode(),
            headers=headers,
        )
        try:
            with urlopen(request, timeout=30) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error:
            return error.code, error.headers, error.read()

    def wait(self, job_id):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status, _, data = self.request("/api/jobs/" + job_id)
            self.assertEqual(status, 200)
            job = json.loads(data)
            if job["status"] in ("complete", "failed", "cancelled"):
                return job
            time.sleep(0.02)
        self.fail("Native job did not finish")

    def test_editor_and_dynamic_bundle(self):
        status, headers, html = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"jobProgress", html)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        status, _, script = self.request("/generator.js")
        self.assertEqual(status, 200)
        self.assertIn(b"GENERATOR_TEMPLATE", script)
        for asset in ("app.js", "export.js", "sand.js", "style.css"):
            self.assertEqual(self.request("/" + asset)[0], 200)

    def test_request_boundaries(self):
        config = {"counts": [1], "min": 0, "max": 0, "format": "txt", "order": "sorted"}
        self.assertEqual(
            self.request("/api/jobs", {"config": config}, {"X-Distribution-Token": "invalid"})[0],
            403,
        )
        self.assertEqual(self.request("/api/session", headers={"Host": "hostile.example"})[0], 403)
        self.assertEqual(
            self.request("/api/session", headers={"Origin": "https://hostile.example"})[0], 403
        )
        self.assertEqual(self.request("/../generator.py")[0], 404)
        self.assertEqual(
            self.request("/api/jobs", {"config": config, "filename": "../escape.txt"})[0], 400
        )
        self.assertEqual(self.request("/api/jobs", {"config": dict(config, counts=[True])})[0], 400)
        self.assertEqual(self.request("/api/jobs", {"config": config, "threads": 9999})[0], 400)

    def test_generate_text_and_stream_download(self):
        if not (shutil.which("g++") or shutil.which("clang++") or shutil.which("cl")):
            self.skipTest("Native compiler not installed")
        config = {"counts": [3, 0, 7], "min": 0, "max": 29, "format": "txt", "order": "sorted"}
        status, _, data = self.request("/api/jobs", {"config": config, "seed": 42})
        self.assertEqual(status, 201, data)
        job = self.wait(json.loads(data)["id"])
        self.assertEqual(job["status"], "complete", job)
        status, headers, content = self.request(job["download"])
        self.assertEqual(status, 200)
        expected = [0, 3, 6] + [20 + i * 10 // 7 for i in range(7)]
        self.assertEqual(list(map(int, content.splitlines())), expected)
        self.assertEqual(len(content), output_size(config))
        self.assertEqual(Path(job["path"]).read_bytes(), content)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(job["progress"], 1)

    def test_progress_and_binary(self):
        if not (shutil.which("g++") or shutil.which("clang++") or shutil.which("cl")):
            self.skipTest("Native compiler not installed")
        config = {
            "counts": [9, 13],
            "min": -100,
            "max": 99,
            "format": "binary",
            "order": "shuffled",
        }
        output, progress = self.root / "raw.bin", self.root / "progress"
        generate(config, output, cache=self.root / "cache", progress=progress, seed=42)
        completed, total, phase = struct.unpack("=QQQ", progress.read_bytes())
        self.assertEqual((completed, total, phase), (1, 1, 2))
        self.assertEqual(len(output.read_bytes()), 22 * 8)
        values = struct.unpack("<" + "q" * 22, output.read_bytes())
        self.assertEqual(
            sorted(values),
            [-100 + i * 100 // 9 for i in range(9)] + [i * 100 // 13 for i in range(13)],
        )

    def test_cancel_removes_partial_and_rejects_second_job(self):
        config = {"counts": [2000000000], "min": 0, "max": 0, "format": "txt", "order": "sorted"}
        status, _, body = self.request("/api/jobs", {"config": config})
        self.assertEqual(status, 201, body)
        job = json.loads(body)
        self.assertEqual(self.request("/api/jobs", {"config": config})[0], 409)
        self.assertEqual(self.request("/api/jobs/" + job["id"] + "/cancel", {})[0], 200)
        job = self.wait(job["id"])
        self.assertEqual(job["status"], "cancelled", job)
        self.assertFalse(Path(job["path"]).exists())
        self.assertEqual(self.request("/api/jobs/" + job["id"] + "/file")[0], 409)
