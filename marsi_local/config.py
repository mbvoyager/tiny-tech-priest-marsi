"""Small, explicit configuration with no dotenv dependency or shell evaluation."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: str | Path) -> None:
    path = Path(path)
    if not path.exists():
        return
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"{path}:{number}: expected NAME=value")
        os.environ.setdefault(key, value.strip().strip('\"\''))


def validate_url(value: str) -> str:
    url = urlsplit(value)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
        raise ValueError("Use an http(s) URL without embedded credentials")
    if url.query or url.fragment or url.path not in ("", "/"):
        raise ValueError("Use the server's base URL, without a path or query")
    return value.rstrip("/")


@dataclass(frozen=True)
class ServerConfig:
    ollama_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3:1.7b"
    token: str = ""
    database: Path = ROOT / "data" / "companion.sqlite3"
    whisper_model: str = "tiny"
    whisper_language: str | None = None
    piper_model: Path | None = None
    threads: int = 3
    timeout: float = 180.0
    demo: bool = False

    @classmethod
    def from_env(cls, demo: bool = False) -> ServerConfig:
        voice = os.getenv("MARSI_PIPER_MODEL", "")
        config = cls(
            ollama_url=validate_url(os.getenv("MARSI_OLLAMA_URL", cls.ollama_url)),
            model=os.getenv("MARSI_MODEL", cls.model),
            token=os.getenv("MARSI_TOKEN", ""),
            database=Path(os.getenv("MARSI_DATABASE", str(cls.database))).expanduser(),
            whisper_model=os.getenv("MARSI_WHISPER_MODEL", "tiny"),
            whisper_language=os.getenv("MARSI_WHISPER_LANGUAGE") or None,
            piper_model=Path(voice).expanduser() if voice else None,
            threads=int(os.getenv("MARSI_CPU_THREADS", "3")),
            timeout=float(os.getenv("MARSI_TIMEOUT", "180")),
            demo=demo,
        )
        if not 1 <= config.threads <= 64 or not 1 <= config.timeout <= 600:
            raise ValueError("CPU threads must be 1..64 and timeout must be 1..600 seconds")
        return config


def validate_bind(host: str, token: str) -> None:
    # Literal loopback only: a DNS name may resolve to a LAN address.
    if host not in ("127.0.0.1", "localhost") and len(token) < 32:
        raise ValueError("A LAN listener requires MARSI_TOKEN with at least 32 characters")
    if token and (not token.isascii() or any(c.isspace() for c in token)):
        raise ValueError("MARSI_TOKEN must be ASCII with no whitespace")
