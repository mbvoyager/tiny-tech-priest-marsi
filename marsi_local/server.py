"""Small authenticated LAN API for one Marsi household companion."""
from __future__ import annotations

import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import logging
import os
import socket
from urllib.parse import parse_qs, urlsplit

from .config import ServerConfig, load_env, validate_bind
from .core import Companion, ServiceError, clean_text, ritual, session_name
from .speech import MAX_AUDIO_BYTES, Speech

LOG = logging.getLogger("marsi.local")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, address, companion):
        self.companion = companion
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    # Short-lived connections keep the implementation and resource use predictable.
    server_version = "Marsi/0.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        # Conversations and credentials must not appear in access logs.
        pass

    def respond(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def authenticated(self):
        token = self.server.companion.config.token
        provided = self.headers.get("Authorization", "")
        if token and not hmac.compare_digest(provided.encode(), ("Bearer " + token).encode()):
            raise ServiceError("A valid Marsi token is required.", 401)

    def read_body(self, limit=MAX_AUDIO_BYTES) -> bytes:
        if self.headers.get("Transfer-Encoding"):
            raise ServiceError("Chunked requests are not supported.", 400)
        length = self.headers.get("Content-Length")
        if length is None:
            raise ServiceError("Content-Length is required.", 411)
        try:
            count = int(length)
        except ValueError as error:
            raise ValueError("Invalid Content-Length") from error
        if count < 0 or count > limit:
            raise ServiceError("Request body exceeds the allowed size.", 413)
        data = self.rfile.read(count)
        if len(data) != count:
            raise ValueError("Incomplete request body")
        return data

    def read_json(self) -> dict:
        if self.headers.get_content_type() != "application/json":
            raise ServiceError("Use Content-Type: application/json.", 415)
        try:
            payload = json.loads(self.read_body(16384))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("Invalid JSON") from error
        if not isinstance(payload, dict):
            raise ValueError("JSON must be an object")
        return payload

    def dispatch(self, method: str):
        try:
            path = urlsplit(self.path).path
            app = self.server.companion
            if method == "GET" and path == "/health":
                return self.respond(200, {"status": "ok", "mode": "demo" if app.config.demo else "local-llm"})
            self.authenticated()
            if method == "GET" and path == "/v1/memory":
                query = parse_qs(urlsplit(self.path).query)
                session = session_name(query.get("session_id", ["pi"])[0])
                with app.exclusive():
                    return self.respond(200, {"session_id": session, "notes": app.memory.notes(session)})
            if (method, path) not in (("POST", "/v1/chat"), ("POST", "/v1/voice"),
                                      ("POST", "/v1/ritual"), ("POST", "/v1/memory"),
                                      ("DELETE", "/v1/session")):
                raise ServiceError("Unknown endpoint.", 404)
            if path == "/v1/voice":
                session = session_name(self.headers.get("X-Marsi-Session", "pi"))
                if self.headers.get_content_type() != "audio/wav":
                    raise ServiceError("Use Content-Type: audio/wav.", 415)
                recording = self.read_body()
                audio_flag = parse_qs(urlsplit(self.path).query).get("want_audio", ["true"])[0]
                if audio_flag not in ("true", "false"):
                    raise ValueError("want_audio must be true or false")
                wants_audio = audio_flag == "true"
                payload = {}
            else:
                payload = self.read_json()
                session = session_name(payload.get("session_id", "pi"))
                wants_audio = payload.get("want_audio", False)
                if type(wants_audio) is not bool:
                    raise ValueError("want_audio must be a boolean")
            with app.exclusive():
                if path == "/v1/session":
                    app.memory.forget(session)
                    return self.respond(200, {"forgotten": session})
                if path == "/v1/memory":
                    app.memory.add_note(session, clean_text(payload.get("text"), 200))
                    return self.respond(200, {"notes": app.memory.notes(session)})
                if path == "/v1/voice":
                    text = clean_text(app.speech.transcribe(recording))
                    result = app.chat(session, text)
                    result["heard"] = text
                elif path == "/v1/chat":
                    result = app.chat(session, clean_text(payload.get("text")))
                else:
                    result = ritual()
                if wants_audio:
                    try:
                        result["audio_base64"] = base64.b64encode(app.speech.synthesize(result["text"])).decode("ascii")
                    except ServiceError as error:
                        result["voice_error"] = str(error)
                self.respond(200, result)
        except ServiceError as error:
            self.respond(error.status, {"error": str(error)})
        except ValueError as error:
            self.respond(400, {"error": str(error)})
        except (socket.timeout, ConnectionError):
            self.close_connection = True
        except Exception:
            LOG.exception("Marsi request failed")
            self.respond(500, {"error": "Internal error. Check the server console."})

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_DELETE(self):
        self.dispatch("DELETE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=".env.server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--demo", action="store_true", help="Use original offline templates instead of Qwen")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if os.name == "posix":
        os.umask(0o077)
    try:
        load_env(args.env)
        config = ServerConfig.from_env(args.demo)
        validate_bind(args.host, config.token)
        app = Companion(config, speech=Speech(config))
        with Server((args.host, args.port), app) as server:
            LOG.info("Marsi listening on %s:%s (%s)", args.host, args.port, "template demo" if args.demo else config.model)
            server.serve_forever()
    except (ValueError, OSError) as error:
        parser.exit(1, f"Cannot start Marsi: {error}\n")
    except KeyboardInterrupt:
        LOG.info("The tiny forge is resting.")


if __name__ == "__main__":
    main()
