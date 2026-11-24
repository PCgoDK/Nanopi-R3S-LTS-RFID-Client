from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class ExtractionRule:
    name: str
    marker: str
    length: int | None = None
    terminator: str | None = None

    def __post_init__(self):
        if not self.name.strip():
            raise ValueError("Rule name must not be empty.")
        if not self.marker:
            raise ValueError("Search text must not be empty.")
        if (self.length is None) == (self.terminator is None):
            raise ValueError("Choose either an extraction length or a termination text.")
        if self.length is not None:
            if not isinstance(self.length, int) or isinstance(self.length, bool) or self.length < 1:
                raise ValueError("Extraction length must be a positive number of characters.")
        elif not isinstance(self.terminator, str) or not self.terminator:
            raise ValueError("Termination text must not be empty.")


def extract_values(data: bytes, rules: list[ExtractionRule]) -> list[dict]:
    text = data.decode("utf-8", errors="surrogateescape")
    results = []
    for rule in rules:
        start = text.find(rule.marker)
        if start < 0:
            results.append({"name": rule.name, "status": "not_found", "value": None})
            continue
        start += len(rule.marker)
        if rule.length is not None:
            end = start + rule.length
            incomplete = end > len(text)
        else:
            end = text.find(rule.terminator, start)
            incomplete = end < 0
        value = text[start:end] if not incomplete else ""
        if incomplete:
            status = "incomplete"
        elif any("\udc80" <= char <= "\udcff" for char in value):
            status = "invalid_utf8"
        else:
            status = "matched"
        results.append({"name": rule.name, "status": status, "value": value if status == "matched" else None})
    return results


class RuleStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def _connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS rules ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, marker TEXT NOT NULL, "
                    "length INTEGER, terminator TEXT, version INTEGER NOT NULL DEFAULT 1)"
                )
                yield connection
        finally:
            connection.close()

    def list(self) -> list[dict]:
        with self._connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT id, name, marker, length, terminator, version FROM rules ORDER BY id"
            )]

    def list_existing(self) -> "list[dict]":
        try:
            self.path.stat()
        except FileNotFoundError:
            return []
        connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in connection.execute(
                "SELECT id, name, marker, length, terminator, version FROM rules ORDER BY id"
            )]
        finally:
            connection.close()

    def save(self, rule: ExtractionRule, rule_id: int | None = None, version: int | None = None) -> int | None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if rule_id is None:
                if connection.execute("SELECT COUNT(*) FROM rules").fetchone()[0] >= 100:
                    raise ValueError("Maximum 100 regler.")
                cursor = connection.execute(
                    "INSERT INTO rules (name, marker, length, terminator) VALUES (?, ?, ?, ?)",
                    (rule.name, rule.marker, rule.length, rule.terminator),
                )
                return cursor.lastrowid
            cursor = connection.execute(
                "UPDATE rules SET name=?, marker=?, length=?, terminator=?, version=version+1 WHERE id=? AND version=?",
                (rule.name, rule.marker, rule.length, rule.terminator, rule_id, version),
            )
            if not cursor.rowcount and connection.execute("SELECT 1 FROM rules WHERE id=?", (rule_id,)).fetchone():
                raise RuleConflictError("Reglen er ændret af en anden bruger. Opdatér listen.")
            return rule_id if cursor.rowcount else None

    def delete(self, rule_id: int, version: int) -> bool:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute("DELETE FROM rules WHERE id=? AND version=?", (rule_id, version))
            if not cursor.rowcount and connection.execute("SELECT 1 FROM rules WHERE id=?", (rule_id,)).fetchone():
                raise RuleConflictError("Reglen er ændret af en anden bruger. Opdatér listen.")
            return cursor.rowcount > 0


class RuleConflictError(Exception):
    pass
