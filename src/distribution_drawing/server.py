"""Small loopback-only companion for direct generation from the editor."""

import json
import mimetypes
import os
import re
import secrets
import shutil
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .bundle import browser_bundle
from .generator import generate, output_size, validate_config

WEB = Path(__file__).resolve().parent / "web"
ACTIVE = {"queued", "building", "generating", "flushing", "cancelling"}


class BusyError(ValueError):
    pass


class Jobs:
    def __init__(self, output_dir, cache):
        self.output_dir = Path(output_dir).resolve()
        self.cache = Path(cache).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.jobs = {}

    def create(self, request):
        config = validate_config(request.get("config"))
        threads = request.get("threads", min(os.cpu_count() or 1, 16))
        seed = request.get("seed")
        if type(threads) is not int or not 1 <= threads <= 256:
            raise ValueError("Choose 1–256 threads.")
        if seed is not None and (type(seed) is not int or not 0 <= seed < 2**64):
            raise ValueError("The seed must be an unsigned 64-bit integer.")
        extension = "bin" if config["format"] in ("u32", "i64") else config["format"]
        filename = request.get("filename") or f"distribution-{config['order']}.{extension}"
        if not isinstance(filename, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", filename
        ):
            raise ValueError(
                "Use a filename containing letters, numbers, dots, underscores or hyphens."
            )
        expected = output_size(config)
        if shutil.disk_usage(self.output_dir).free < expected + 16 * 1024 * 1024:
            raise ValueError("Not enough free space in the output folder for this dataset.")
        with self.lock:
            if any(job["status"] in ACTIVE for job in self.jobs.values()):
                raise BusyError(
                    "A dataset is already being generated. Cancel it or wait for it to finish."
                )
            job_id = secrets.token_hex(8)
            folder = self.output_dir / job_id
            folder.mkdir()
            job = {
                "id": job_id,
                "status": "queued",
                "progress": 0.0,
                "count": sum(config["counts"]),
                "bytes": expected,
                "filename": filename,
                "path": str(folder / filename),
                "created": time.time(),
                "error": None,
                "_folder": folder,
                "_partial": folder / ".dataset.part",
                "_progress": folder / ".progress",
                "_cancel": threading.Event(),
                "_process": None,
            }
            self.jobs[job_id] = job
            threading.Thread(
                target=self._run, args=(job, config, threads, seed), daemon=True
            ).start()
            return self.snapshot(job_id)

    def _run(self, job, config, threads, seed):
        def stage(value):
            with self.lock:
                if not job["_cancel"].is_set():
                    job["status"] = value

        def process(value):
            with self.lock:
                job["_process"] = value
                if job["_cancel"].is_set() and value.poll() is None:
                    value.terminate()

        try:
            result = generate(
                config,
                job["_partial"],
                cache=self.cache,
                threads=threads,
                seed=seed,
                progress=job["_progress"],
                on_stage=stage,
                on_process=process,
                cancelled=job["_cancel"].is_set,
            )
            with self.lock:
                if job["_cancel"].is_set():
                    raise RuntimeError("Generation cancelled.")
                job["_partial"].replace(job["path"])
                job.update(result)
                job["status"] = "complete"
                job["progress"] = 1.0
        except Exception as error:
            with self.lock:
                job["status"] = "cancelled" if job["_cancel"].is_set() else "failed"
                job["error"] = None if job["_cancel"].is_set() else str(error)
        finally:
            with self.lock:
                job["_process"] = None
                job["elapsed"] = time.time() - job["created"]
                for path in (job["_partial"], job["_progress"]):
                    path.unlink(missing_ok=True)

    def snapshot(self, job_id):
        with self.lock:
            job = self.jobs[job_id]
            if job["status"] in ACTIVE and job["_progress"].exists():
                try:
                    data = job["_progress"].read_bytes()
                    if len(data) == 24:
                        completed, total, phase = struct.unpack("=QQQ", data)
                        if total:
                            job["progress"] = max(
                                job["progress"], min(0.99, completed / total * 0.99)
                            )
                        if phase == 1 and not job["_cancel"].is_set():
                            job["status"] = "flushing"
                except OSError:
                    pass
            public = {key: value for key, value in job.items() if not key.startswith("_")}
            public["elapsed"] = job.get("elapsed", time.time() - job["created"])
            if job["status"] == "complete":
                public["download"] = f"/api/jobs/{job_id}/file"
            return public

    def cancel(self, job_id):
        with self.lock:
            job = self.jobs[job_id]
            if job["status"] in ACTIVE:
                job["_cancel"].set()
                job["status"] = "cancelling"
                process = job["_process"]
                if process is not None and process.poll() is None:
                    process.terminate()
            return self.snapshot(job_id)

    def close(self):
        with self.lock:
            for job_id in list(self.jobs):
                self.cancel(job_id)


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, jobs):
        super().__init__(address, Handler)
        self.jobs = jobs
        self.token = secrets.token_urlsafe(32)
        self.bundle = browser_bundle().encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _trusted(self):
        host = self.headers.get("Host", "")
        port = self.server.server_address[1]
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return False
        origin = self.headers.get("Origin")
        return (
            not origin or origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")
        ) and self.headers.get("Sec-Fetch-Site") != "cross-site"

    def _headers(self, status, content_type, length):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )

    def _json(self, status, value):
        data = json.dumps(value).encode()
        self._headers(status, "application/json; charset=utf-8", len(data))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._trusted():
            self._json(403, {"error": "Use the local app address."})
            return
        path = urlsplit(self.path).path
        if path == "/api/session":
            self._json(
                200,
                {
                    "token": self.server.token,
                    "output_dir": str(self.server.jobs.output_dir),
                    "threads": min(os.cpu_count() or 1, 16),
                    "engine": "local",
                },
            )
            return
        if path == "/api/jobs":
            with self.server.jobs.lock:
                jobs = [self.server.jobs.snapshot(key) for key in self.server.jobs.jobs]
            self._json(200, {"jobs": jobs[-20:]})
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{16})(/file)?", path)
        if match:
            try:
                job = self.server.jobs.snapshot(match[1])
                if match[2]:
                    if job["status"] != "complete":
                        self._json(409, {"error": "The dataset is not ready."})
                        return
                    with open(job["path"], "rb") as source:
                        self._headers(
                            200, "application/octet-stream", os.fstat(source.fileno()).st_size
                        )
                        self.send_header(
                            "Content-Disposition", f'attachment; filename="{job["filename"]}"'
                        )
                        self.end_headers()
                        shutil.copyfileobj(source, self.wfile, 1024 * 1024)
                else:
                    self._json(200, job)
            except KeyError:
                self._json(404, {"error": "Dataset not found."})
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if path == "/generator.js":
            self._headers(200, "text/javascript; charset=utf-8", len(self.server.bundle))
            self.end_headers()
            self.wfile.write(self.server.bundle)
            return
        target = (WEB / (unquote(path).lstrip("/") or "index.html")).resolve()
        if WEB not in target.parents or not target.is_file():
            self._json(404, {"error": "Not found."})
            return
        data = target.read_bytes()
        self._headers(
            200, mimetypes.guess_type(str(target))[0] or "application/octet-stream", len(data)
        )
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not self._trusted() or not secrets.compare_digest(
            self.headers.get("X-Distribution-Token", ""), self.server.token
        ):
            self._json(403, {"error": "Open the local editor before starting a job."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024 * 1024:
                raise ValueError("Invalid request size.")
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("Expected JSON.")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Expected an object.")
            path = urlsplit(self.path).path
            if path == "/api/jobs":
                self._json(201, self.server.jobs.create(body))
                return
            match = re.fullmatch(r"/api/jobs/([a-f0-9]{16})/cancel", path)
            if match:
                self._json(200, self.server.jobs.cancel(match[1]))
                return
            self._json(404, {"error": "Not found."})
        except KeyError:
            self._json(404, {"error": "Dataset not found."})
        except BusyError as error:
            self._json(409, {"error": str(error)})
        except (ValueError, TypeError) as error:
            self._json(400, {"error": str(error)})
        except OSError as error:
            self._json(500, {"error": str(error)})


def make_server(port, output_dir, cache):
    return LocalServer(("127.0.0.1", port), Jobs(output_dir, cache))
