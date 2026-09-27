"""Loopback-only HTTP interface with per-process request authorization."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from .engine import Engine
from .speech import Speech


class JarvisServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, engine: Engine, speech: Speech):
        self.engine, self.speech = engine, speech
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)
        self.port = self.server_address[1]


class Handler(BaseHTTPRequestHandler):
    server: JarvisServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        # Requests can contain private prompts. Keep the console quiet.
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header("Permissions-Policy", "microphone=(self), camera=(), geolocation=()")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, body: dict):
        self._send(status, json.dumps(body, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _origin_ok(self) -> bool:
        hosts = {f"127.0.0.1:{self.server.port}", f"localhost:{self.server.port}"}
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        return host in hosts and (origin is None or origin == f"http://{host}")

    def do_GET(self):
        if not self._origin_ok():
            self._json(403, {"error": "Only the local Jev-Jarvis page can access this server."})
            return
        path = urlsplit(self.path).path
        if path == "/api/bootstrap":
            engine = self.server.engine
            config = engine.planner.config
            scenes = [{"id": key, "title": s["title"], "description": s["description"],
                       "prompt": f"routine {key}"} for key, s in config["scenes"].items()]
            self._json(200, {"token": self.server.token,
                "mode": "live" if engine.executor.live else "rehearsal",
                "provider": engine.planner.decider.name, "planner": engine.planner.name,
                "voice_enabled": self.server.speech.enabled,
                "apps": list(config["apps"]), "contacts": list(config["contacts"]),
                "shortcuts": list(config.get("shortcuts", {})),
                "scenes": scenes, **engine.snapshot()})
        elif path == "/api/history":
            self._json(200, self.server.engine.snapshot())
        elif path == "/api/health":
            self._json(200, {"ok": True})
        elif path in {"/", "/index.html", "/app.css", "/app.js", "/static/app.css", "/static/app.js"}:
            file = Path(__file__).parent / "static" / ("index.html" if path == "/" else path.rsplit("/", 1)[-1])
            self._send(200, file.read_bytes(), (mimetypes.guess_type(str(file))[0] or "text/plain") + "; charset=utf-8")
        else:
            self._json(404, {"error": "Not found."})

    def do_POST(self):
        if not self._origin_ok() or not secrets.compare_digest(self.headers.get("X-Jarvis-Token", ""), self.server.token):
            self.close_connection = True
            self._json(403, {"error": "Session expired or unauthorized. Reload the local page."})
            return
        path = urlsplit(self.path).path
        limit = 8 * 1024 * 1024 if path == "/api/transcribe" else 16_384
        try:
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("Chunked requests are not supported.")
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= limit:
                self.close_connection = True
                raise ValueError("Request body is missing or too large.")
            payload = self.rfile.read(size)
            if len(payload) != size:
                raise ValueError("Incomplete request.")
            if path == "/api/transcribe":
                if not self.headers.get("Content-Type", "").startswith(("audio/", "video/webm")):
                    raise ValueError("Expected an audio recording.")
                self._json(200, {"text": self.server.speech.transcribe(payload)})
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("Expected a JSON request.")
            data = json.loads(payload)
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object.")
            if path == "/api/plan":
                if set(data) != {"text"}:
                    raise ValueError("Provide only the request text.")
                result = self.server.engine.plan(data["text"])
            elif path in {"/api/execute", "/api/cancel", "/api/undo"}:
                if set(data) != {"id"} or not isinstance(data["id"], str):
                    raise ValueError("Confirm only a stored plan or receipt ID.")
                method = {"/api/execute": "execute", "/api/cancel": "cancel", "/api/undo": "undo"}[path]
                result = getattr(self.server.engine, method)(data["id"])
            else:
                self._json(404, {"error": "Not found."})
                return
            self._json(200, result)
        except (ValueError, UnicodeDecodeError) as exc:
            self._json(400, {"error": str(exc)})
        except OSError:
            self._json(500, {"error": "A local operation failed. Check Activity before retrying; an action may have completed."})
        except Exception:
            self._json(500, {"error": "Unexpected error. Check Activity before trying again."})
