import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet


class Store:
    def __init__(self, database: Path, key: str | None):
        database.parent.mkdir(parents=True, exist_ok=True)
        self.database = database
        # Mock mode persists its development key in a private file for local restarts.
        if key is None:
            key_file = database.with_suffix(".key")
            if not key_file.exists():
                try:
                    fd = os.open(key_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    with os.fdopen(fd, "wb") as handle: handle.write(Fernet.generate_key())
                except FileExistsError:
                    pass
            key = key_file.read_text()
        self.cipher = Fernet(key.encode())
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS connections (id TEXT PRIMARY KEY, owner TEXT NOT NULL, data BLOB NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS states (hash TEXT PRIMARY KEY, expires REAL NOT NULL, data BLOB NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, data BLOB NOT NULL)")
        os.chmod(database, 0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.database, timeout=20)
        try:
            with db: yield db
        finally:
            db.close()

    def encrypt(self, data):
        return self.cipher.encrypt(json.dumps(data).encode())

    def decrypt(self, data):
        return json.loads(self.cipher.decrypt(data))

    def create_connection(self, owner, token, mode):
        connection_id = secrets.token_urlsafe(24)
        with self.db() as db:
            db.execute("INSERT INTO connections VALUES (?, ?, ?)", (connection_id, owner, self.encrypt({"refresh_token": token, "mode": mode, "revoked": False, "accounts": [], "selected": []})))
        return connection_id

    def connection(self, connection_id, owner):
        with self.db() as db:
            row = db.execute("SELECT data FROM connections WHERE id=? AND owner=?", (connection_id, owner)).fetchone()
        return self.decrypt(row[0]) if row else None

    def update(self, connection_id, owner, data):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM connections WHERE id=? AND owner=?", (connection_id, owner)).fetchone()
            if row is None or self.decrypt(row[0]).get("revoked"):
                return False
            db.execute("UPDATE connections SET data=? WHERE id=? AND owner=?", (self.encrypt(data), connection_id, owner))
            return True

    def revoke(self, connection_id, owner):
        with self.db() as db:
            db.execute("UPDATE connections SET data=? WHERE id=? AND owner=?", (self.encrypt({"revoked": True, "accounts": [], "selected": [], "refresh_token": None}), connection_id, owner))
            db.execute("DELETE FROM reports WHERE connection_id=?", (connection_id,))

    def new_state(self, owner, verifier):
        state = secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute("DELETE FROM states WHERE expires < ?", (time.time(),))
            db.execute("INSERT INTO states VALUES (?, ?, ?)", (hashlib.sha256(state.encode()).hexdigest(), time.time() + 600, self.encrypt({"owner": owner, "verifier": verifier})))
        return state

    def pop_state(self, state):
        key = hashlib.sha256(state.encode()).hexdigest()
        with self.db() as db:
            # Atomic consumption across workers/processes.
            row = db.execute("DELETE FROM states WHERE hash=? RETURNING expires,data", (key,)).fetchone()
        return self.decrypt(row[1]) if row and row[0] >= time.time() else None

    def save_report(self, connection_id, owner, report):
        report_id = secrets.token_urlsafe(24)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM connections WHERE id=? AND owner=?", (connection_id, owner)).fetchone()
            data = self.decrypt(row[0]) if row else {}
            if data.get("revoked") or report["customer_id"] not in data.get("selected", []):
                return None
            db.execute("INSERT INTO reports VALUES (?, ?, ?)", (report_id, connection_id, self.encrypt(report)))
        return report_id

    def report(self, report_id, connection_id):
        with self.db() as db:
            row = db.execute("SELECT data FROM reports WHERE id=? AND connection_id=?", (report_id, connection_id)).fetchone()
        return self.decrypt(row[0]) if row else None
