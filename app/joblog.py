from contextlib import contextmanager
import csv
import io
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path


RETENTION_SECONDS = 24 * 60 * 60


class JobLog:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def _connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS jobs ("
                    "id TEXT PRIMARY KEY, created_at REAL NOT NULL, incomplete INTEGER NOT NULL, "
                    "extraction_state TEXT NOT NULL, results TEXT, error TEXT, resolution TEXT NOT NULL DEFAULT 'pending')"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS observations ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, epc TEXT NOT NULL, "
                    "first_seen REAL NOT NULL, last_seen REAL NOT NULL, reads INTEGER NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS job_epcs ("
                    "job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE, "
                    "observation_id INTEGER NOT NULL, epc TEXT NOT NULL, observed_at REAL NOT NULL, "
                    "PRIMARY KEY(job_id, observation_id))"
                )
                connection.execute("CREATE INDEX IF NOT EXISTS jobs_created_at ON jobs(created_at, id)")
                connection.execute("CREATE INDEX IF NOT EXISTS observations_epc ON observations(epc, id)")
                connection.execute("CREATE INDEX IF NOT EXISTS observations_last_seen ON observations(last_seen)")
                yield connection
        finally:
            connection.close()

    def add_job(self, job_id, created_at, incomplete, extraction):
        results = extraction["results"]
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO jobs (id, created_at, incomplete, extraction_state, results, error) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, created_at, int(incomplete), extraction["state"],
                 json.dumps(results, ensure_ascii=False) if results is not None else None, extraction["error"]),
            )

    def mark_incomplete(self, job_id):
        with self._connection() as connection:
            connection.execute("UPDATE jobs SET incomplete=1 WHERE id=?", (job_id,))

    def add_observation(self, epc, observed_at):
        return self.add_observations([(epc, observed_at)])[0]

    def add_observations(self, observations):
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            ids = []
            for epc, observed_at in observations:
                previous = connection.execute(
                    "SELECT id, last_seen FROM observations WHERE epc=? ORDER BY id DESC LIMIT 1", (epc,)
                ).fetchone()
                if previous and 0 <= observed_at - previous["last_seen"] <= 0.5:
                    connection.execute(
                        "UPDATE observations SET last_seen=?, reads=reads+1 WHERE id=?",
                        (observed_at, previous["id"]),
                    )
                    ids.append(previous["id"])
                else:
                    ids.append(connection.execute(
                        "INSERT INTO observations (epc, first_seen, last_seen, reads) VALUES (?, ?, ?, 1)",
                        (epc, observed_at, observed_at),
                    ).lastrowid)
            return ids

    def list_jobs(self, limit=100, before=None, upper=None):
        with self._connection() as connection:
            conditions = []
            parameters = []
            if upper is not None:
                conditions.append("created_at <= ?")
                parameters.append(upper)
            if before is not None:
                conditions.append("(created_at < ? OR (created_at = ? AND id < ?))")
                parameters.extend((before[0], before[0], before[1]))
            where = " WHERE " + " AND ".join(conditions) if conditions else ""
            parameters.append(limit)
            rows = connection.execute(
                "SELECT * FROM jobs" + where + " ORDER BY created_at DESC, id DESC LIMIT ?", parameters
            ).fetchall()
            result = []
            for row in rows:
                job = dict(row)
                job["incomplete"] = bool(job["incomplete"])
                job["results"] = json.loads(job["results"]) if job["results"] is not None else None
                if job["incomplete"] and job["results"] is not None:
                    job["results"] = [{"name": value["name"], "status": "unreliable", "value": None}
                                      for value in job["results"]]
                job["epcs"] = [dict(link) for link in connection.execute(
                    "SELECT observation_id, epc, observed_at FROM job_epcs WHERE job_id=? ORDER BY observed_at",
                    (job["id"],),
                )]
                result.append(job)
            return result

    def list_observations(self, limit=200):
        with self._connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT id, epc, first_seen, last_seen, reads FROM observations "
                "ORDER BY last_seen DESC, id DESC LIMIT ?", (limit,)
            )]

    def link(self, job_id, observation_id):
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not connection.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone():
                return False
            observation = connection.execute(
                "SELECT epc, first_seen FROM observations WHERE id=?", (observation_id,)
            ).fetchone()
            if observation is None:
                return False
            connection.execute(
                "INSERT OR IGNORE INTO job_epcs (job_id, observation_id, epc, observed_at) VALUES (?, ?, ?, ?)",
                (job_id, observation_id, observation["epc"], observation["first_seen"]),
            )
            connection.execute("UPDATE jobs SET resolution='linked' WHERE id=?", (job_id,))
            return True

    def mark_no_tag(self, job_id):
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM job_epcs WHERE job_id=?", (job_id,)).fetchone():
                raise ValueError("Fjern tilknyttede EPC'er før NoTag.")
            return bool(connection.execute(
                "UPDATE jobs SET resolution='no_tag' WHERE id=?", (job_id,)
            ).rowcount)

    def clear_no_tag(self, job_id):
        with self._connection() as connection:
            return bool(connection.execute(
                "UPDATE jobs SET resolution='pending' WHERE id=? AND resolution='no_tag'", (job_id,)
            ).rowcount)

    def unlink(self, job_id, observation_id):
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            deleted = connection.execute(
                "DELETE FROM job_epcs WHERE job_id=? AND observation_id=?", (job_id, observation_id)
            ).rowcount
            if deleted and not connection.execute(
                "SELECT 1 FROM job_epcs WHERE job_id=?", (job_id,)
            ).fetchone():
                connection.execute("UPDATE jobs SET resolution='pending' WHERE id=?", (job_id,))
            return bool(deleted)

    def prune(self, now=None):
        cutoff = (time.time() if now is None else now) - RETENTION_SECONDS
        with self._connection() as connection:
            connection.execute("DELETE FROM jobs WHERE created_at < ?", (cutoff,))
            connection.execute("DELETE FROM observations WHERE last_seen < ?", (cutoff,))

    def csv_rows(self):
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["job_id", "tid_utc", "status", "udtraek_status", "printdata_usikre",
                         "udtraek_fejl", "udtraek", "epc", "rfid_tid_utc"])
        yield "\ufeff" + output.getvalue()
        before = None
        upper = time.time()
        while True:
            jobs = self.list_jobs(limit=100, before=before, upper=upper)
            if not jobs:
                break
            for job in jobs:
                results = json.dumps(job["results"], ensure_ascii=False) if job["results"] is not None else ""
                for link in job["epcs"] or [None]:
                    output.seek(0)
                    output.truncate(0)
                    writer.writerow([
                        job["id"], datetime.fromtimestamp(job["created_at"], timezone.utc).isoformat(),
                        job["resolution"], job["extraction_state"], job["incomplete"],
                        job["error"] or "", results, link["epc"] if link else "",
                        datetime.fromtimestamp(link["observed_at"], timezone.utc).isoformat() if link else "",
                    ])
                    yield output.getvalue()
            before = (jobs[-1]["created_at"], jobs[-1]["id"])
