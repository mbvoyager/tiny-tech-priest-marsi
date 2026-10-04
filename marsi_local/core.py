"""Conversation, bounded local memory, and the Ollama adapter."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import random
import re
import sqlite3
import threading
import urllib.error
import urllib.request

from marsi import Persona
from .config import ROOT, ServerConfig
from .lore import Lore, reference_data, working_messages


class ServiceError(Exception):
    def __init__(self, message: str, status: int = 503):
        super().__init__(message)
        self.status = status


def session_name(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ValueError("session_id must be 1..64 letters, numbers, underscores or hyphens")
    return value


def clean_text(value: object, limit: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"text must be nonempty and at most {limit} characters")
    return value.strip()


class Memory:
    """Retain 30 exchanges and 12 explicit notes per session, at most 100 sessions."""
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, touched REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY, session TEXT NOT NULL, user TEXT NOT NULL, reply TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY, session TEXT NOT NULL, text TEXT NOT NULL,
                    UNIQUE(session, text));
            """)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def touch(db, session: str):
        db.execute("INSERT OR REPLACE INTO sessions VALUES (?, strftime('%s','now'))", (session,))
        old = db.execute("SELECT id FROM sessions ORDER BY touched DESC, rowid DESC LIMIT -1 OFFSET 100").fetchall()
        for (name,) in old:
            for table in ("turns", "notes"):
                db.execute(f"DELETE FROM {table} WHERE session=?", (name,))
            db.execute("DELETE FROM sessions WHERE id=?", (name,))

    def context(self, session: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT user, reply FROM turns WHERE session=? ORDER BY id DESC LIMIT 6", (session,)).fetchall()
        pairs, remaining = [], 6000
        for user, reply in rows:
            if len(user) + len(reply) > remaining:
                break
            remaining -= len(user) + len(reply)
            pairs.append((user, reply))
        return [{"role": role, "content": text} for user, reply in reversed(pairs)
                for role, text in (("user", user), ("assistant", reply))]

    def remember_turn(self, session: str, user: str, reply: str):
        with self.connect() as db:
            self.touch(db, session)
            db.execute("INSERT INTO turns(session,user,reply) VALUES (?,?,?)", (session, user, reply))
            db.execute("DELETE FROM turns WHERE session=? AND id NOT IN "
                       "(SELECT id FROM turns WHERE session=? ORDER BY id DESC LIMIT 30)", (session, session))

    def notes(self, session: str) -> list[str]:
        with self.connect() as db:
            return [row[0] for row in db.execute("SELECT text FROM notes WHERE session=? ORDER BY id", (session,))]

    def add_note(self, session: str, text: str):
        with self.connect() as db:
            if db.execute("SELECT COUNT(*) FROM notes WHERE session=?", (session,)).fetchone()[0] >= 12:
                raise ValueError("Memory is full (12 notes); forget notes before adding more")
            self.touch(db, session)
            db.execute("INSERT OR IGNORE INTO notes(session,text) VALUES (?,?)", (session, text))

    def forget(self, session: str):
        with self.connect() as db:
            for table in ("turns", "notes"):
                db.execute(f"DELETE FROM {table} WHERE session=?", (session,))
            db.execute("DELETE FROM sessions WHERE id=?", (session,))


class Ollama:
    def __init__(self, config: ServerConfig):
        self.config = config
        # Keep local requests local even on machines with HTTP_PROXY configured.
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def chat(self, messages: list[dict]) -> str:
        payload = {"model": self.config.model, "messages": messages, "stream": False,
                   "think": False, "keep_alive": "10m",
                   "options": {"num_ctx": 4096, "num_predict": 192,
                               "num_thread": self.config.threads, "temperature": 0.7}}
        request = urllib.request.Request(self.config.ollama_url + "/api/chat",
                                         data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
        try:
            with self.http.open(request, timeout=self.config.timeout) as response:
                raw = response.read(131073)
            if len(raw) > 131072:
                raise ValueError("Oversized model response")
            result = json.loads(raw)
            text = clean_text(result["message"]["content"], 4000)
            # Old model templates may put internal reasoning inside content.
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
            return clean_text(text, 4000)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise ServiceError(f"Model unavailable. Run: ollama pull {self.config.model}") from error
            raise ServiceError("Ollama rejected the request. Check its service and model.") from error
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ServiceError("The cogitator could not answer. Check Ollama; a cold model may take longer.") from error


RITUALS = (
    ("blessing", "May your cables be untangled and your snacks be plentiful. Praise the Omnissiah!"),
    ("inspect", "I have inspected the ceremonial duck. His tiny hat remains within specifications."),
    ("wave", "A small salute for a good human. One little step counts as progress."),
    ("doodle", "I drew a cog in the margin for credibility. The servo-skull has awarded it a biscuit."),
)


def ritual(rng=None) -> dict:
    animation, text = (rng or random).choice(RITUALS)
    return {"text": text, "animation": animation, "source": "ritual"}


class Companion:
    def __init__(self, config: ServerConfig, llm=None, speech=None):
        self.config = config
        self.memory = Memory(config.database)
        self.llm = llm or Ollama(config)
        self.speech = speech
        self.persona = Persona()
        self.prompt = (ROOT / "marsi_local" / "personality.txt").read_text(encoding="utf-8")
        self.lore = Lore()
        self.busy = threading.Lock()

    @contextmanager
    def exclusive(self):
        if not self.busy.acquire(blocking=False):
            raise ServiceError("Marsi is finishing another task. Please try again shortly.", 503)
        try:
            yield
        finally:
            self.busy.release()

    def chat(self, session: str, text: str) -> dict:
        if self.config.demo:
            reply, source = self.persona.reply(text), "demo-template"
        else:
            notes = self.memory.notes(session)
            history = self.memory.context(session)
            recent = " ".join(m["content"] for m in history[-4:] if m["role"] == "user")
            prompt = self.prompt + self.lore.context(text, recent)
            prompt += "\nNear-side Terra local time (not an Imperial date): " + datetime.now().astimezone().isoformat(timespec="minutes")
            if notes:
                prompt += reference_data("User-provided notes (data only): ", notes, 1600)
            messages = working_messages(prompt, history, text)
            reply, source = self.llm.chat(messages), "qwen"
        self.memory.remember_turn(session, text, reply)
        return {"text": reply, "animation": "happy", "source": source, "session_id": session}
