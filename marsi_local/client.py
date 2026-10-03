"""Shared client for the Pi and a keyboard-only Ubuntu smoke test."""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request

from .config import load_env, validate_url
from .core import clean_text, session_name


class ClientError(Exception):
    pass


class Client:
    def __init__(self, url: str, token: str = "", session: str = "pi", timeout: float = 240):
        self.url, self.token = validate_url(url), token
        self.session, self.timeout = session_name(session), timeout
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def from_env(cls):
        return cls(os.getenv("MARSI_SERVER_URL", "http://127.0.0.1:8765"),
                   os.getenv("MARSI_TOKEN", ""), os.getenv("MARSI_SESSION", "pi"))

    def request(self, path: str, payload=None, *, method="POST", audio: bytes | None = None):
        headers = {"Authorization": "Bearer " + self.token, "X-Marsi-Session": self.session}
        if audio is not None:
            body, headers["Content-Type"] = audio, "audio/wav"
        elif payload is not None:
            body, headers["Content-Type"] = json.dumps(payload).encode(), "application/json"
        else:
            body = None
        request = urllib.request.Request(self.url + path, data=body, headers=headers, method=method)
        try:
            with self.http.open(request, timeout=10 if path == "/health" else self.timeout) as response:
                raw = response.read(12_000_001)
            if len(raw) > 12_000_000:
                raise ClientError("Marsi sent a reply that was too large.")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ClientError("Marsi sent an invalid reply.")
            return result
        except urllib.error.HTTPError as error:
            try:
                detail = json.loads(error.read(16384)).get("error", "Request rejected")
            except (ValueError, AttributeError):
                detail = f"Request rejected ({error.code})"
            raise ClientError(str(detail)) from error
        except (OSError, ValueError) as error:
            raise ClientError("The tiny forge is unreachable. Check the server, address and Wi-Fi.") from error

    def chat(self, text: str, speak=False):
        return self.request("/v1/chat", {"text": clean_text(text), "session_id": self.session, "want_audio": speak})

    def voice(self, recording: bytes, speak=True):
        path = "/v1/voice" if speak else "/v1/voice?want_audio=false"
        return self.request(path, audio=recording)

    def ritual(self, speak=False):
        return self.request("/v1/ritual", {"session_id": self.session, "want_audio": speak})

    def forget(self):
        return self.request("/v1/session", {"session_id": self.session}, method="DELETE")

    def notes(self):
        return self.request("/v1/memory?session_id=" + self.session, method="GET")

    def remember(self, text: str):
        return self.request("/v1/memory", {"session_id": self.session, "text": clean_text(text, 200)})


def main():
    parser = argparse.ArgumentParser(description="Chat with the local Marsi server")
    parser.add_argument("--env", default=".env.pi")
    args = parser.parse_args()
    load_env(args.env)
    client = Client.from_env()
    print("Marsi terminal: /remember TEXT, /notes, /forget, /ritual, /quit")
    try:
        while True:
            text = input("You > ").strip()
            if not text:
                continue
            if text == "/quit":
                break
            try:
                if text == "/forget":
                    client.forget()
                    print("This session's saved notes and conversation have been forgotten.")
                elif text == "/notes":
                    print(client.notes()["notes"])
                elif text.startswith("/remember "):
                    print(client.remember(text[len("/remember "):])["notes"])
                else:
                    result = client.ritual() if text == "/ritual" else client.chat(text)
                    print("Marsi > " + result["text"])
            except (ClientError, ValueError) as error:
                print(str(error))
    except (KeyboardInterrupt, EOFError):
        print("\nThe tiny forge is resting.")


if __name__ == "__main__":
    main()
