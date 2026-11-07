import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self.lock = threading.RLock()
        with self.connect() as con:
            # This single-worker training app does not need concurrent WAL checkpoints.
            # Python runtimes can bundle SQLite releases affected by the WAL-reset bug.
            con.execute('PRAGMA journal_mode=DELETE')
            con.execute('CREATE TABLE IF NOT EXISTS documents (kind TEXT, id TEXT, body TEXT NOT NULL, PRIMARY KEY(kind,id))')

    @contextmanager
    def connect(self, write=False):
        with self.lock:
            con = sqlite3.connect(self.path, timeout=30)
            try:
                if write:
                    con.execute('BEGIN IMMEDIATE')
                yield con
                con.commit()
            except Exception:
                con.rollback()
                raise
            finally:
                con.close()

    def get(self, kind, id, con=None):
        if con is None:
            with self.connect() as c:
                return self.get(kind, id, c)
        row = con.execute('SELECT body FROM documents WHERE kind=? AND id=?', (kind,id)).fetchone()
        if row is None:
            raise KeyError(id)
        return json.loads(row[0])

    def all(self, kind, con=None):
        if con is None:
            with self.connect() as c:
                return self.all(kind, c)
        return [json.loads(r[0]) for r in con.execute('SELECT body FROM documents WHERE kind=? ORDER BY id', (kind,))]

    def put(self, kind, id, body, con=None):
        if con is None:
            with self.connect(write=True) as c:
                return self.put(kind,id,body,c)
        con.execute('INSERT INTO documents VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body',
                    (kind, id, json.dumps(body, ensure_ascii=False, allow_nan=False)))
