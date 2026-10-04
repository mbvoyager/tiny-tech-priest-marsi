"""Local, topic-selected canon reference; no network calls or model training."""
from __future__ import annotations

import json
from pathlib import Path
import re


class Lore:
    def __init__(self, path: Path | None = None):
        data = json.loads((path or Path(__file__).with_name("lore.json")).read_text(encoding="utf-8"))
        self.foundations = data["foundations"]
        self.records = data["records"]

    @staticmethod
    def score(record, text):
        return sum(len(alias.split()) + 1 for alias in record["aliases"]
                   if re.search(r"(?<!\w)" + re.escape(alias.casefold()) + r"s?(?!\w)",
                                text.casefold().replace("\u2019", "'")))

    def context(self, query: str, recent: str = "", limit: int = 2800) -> str:
        header = "\nWarhammer 40,000 canon reference (MARSI's machine-flow story is separate):\n"
        text = header + "\n".join("- " + fact for fact in self.foundations)
        ranked = sorted(enumerate(self.records),
                        key=lambda pair: (4 * self.score(pair[1], query) + self.score(pair[1], recent), -pair[0]),
                        reverse=True)
        selected = [record for _, record in ranked
                    if self.score(record, query) or self.score(record, recent)][:3]
        if not selected:
            selected = [self.records[0]]
        for record in selected:
            block = "\n[" + record["title"] + "]\n" + "\n".join("- " + fact for fact in record["facts"])
            if len(text) + len(block) <= limit:
                text += block
        return text


def reference_data(label: str, values: list[str], limit: int) -> str:
    """Budget optional context without cutting JSON or individual notes in half."""
    kept = []
    for value in reversed(values):
        candidate = [value] + kept
        if len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) <= limit:
            kept = candidate
    return "\n" + label + json.dumps(kept, ensure_ascii=False) if kept else ""


def working_messages(prompt: str, history: list[dict], query: str) -> list[dict]:
    """Budget newest complete exchanges for the model's 4K context.

    UTF-8 bytes account for multilingual and token-heavy history. The authored
    system reference and current question always take precedence over history.
    This is a budget heuristic, not the model's exact tokenizer.
    """
    remaining = max(0, 10500 - len(prompt.encode("utf-8")) - len(query.encode("utf-8")))
    kept = []
    for i in range(len(history) - 2, -1, -2):
        pair = history[i:i + 2]
        size = sum(len(message["content"].encode("utf-8")) + 32 for message in pair)
        if size > remaining:
            break
        remaining -= size
        kept[0:0] = pair
    return [{"role": "system", "content": prompt}] + kept + [{"role": "user", "content": query}]
