"""Small, instance-local user store for the dashboard. Direct port of
UrraHosting-MinecraftServer's dashboard/app/services/users.py."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash


class UserStore:
    def __init__(self, path: Path, initial_username: str, initial_password: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._create_schema()
        # APP_USER remains the bootstrap administrator for backward compatibility.
        if not self.get(initial_username):
            self.create(initial_username, initial_password, "admin")

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _create_schema(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin', 'operator')),
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            conn.commit()

    def get(self, username: str) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT username, role, active, created_at FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None

    def authenticate(self, username: str, password: str) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT username, password_hash, role, active FROM users WHERE username = ?", (username,)).fetchone()
        if not row or not row["active"] or not check_password_hash(row["password_hash"], password):
            return None
        return {"username": row["username"], "role": row["role"]}

    def list(self) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT username, role, active, created_at FROM users ORDER BY username COLLATE NOCASE").fetchall()
        return [dict(row) for row in rows]

    def create(self, username: str, password: str, role: str) -> None:
        self._validate(username, password, role)
        with closing(self._connect()) as conn:
            try:
                conn.execute(
                    "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)", (username, generate_password_hash(password), role)
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Ese nombre de usuario ya existe") from exc
            conn.commit()

    def update(self, username: str, *, password: str | None = None, role: str | None = None, active: bool | None = None) -> None:
        existing = self.get(username)
        if not existing:
            raise ValueError("Usuario no encontrado")
        values, fields = [], []
        if password is not None:
            self._validate(username, password, existing["role"])
            fields.append("password_hash = ?")
            values.append(generate_password_hash(password))
        if role is not None:
            if role not in {"admin", "operator"}:
                raise ValueError("Rol invalido")
            fields.append("role = ?")
            values.append(role)
        if active is not None:
            fields.append("active = ?")
            values.append(int(active))
        if not fields:
            return
        values.append(username)
        with closing(self._connect()) as conn:
            conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE username = ?", values)
            conn.commit()

    @staticmethod
    def _validate(username: str, password: str, role: str) -> None:
        if not 3 <= len(username.strip()) <= 64:
            raise ValueError("El usuario debe tener entre 3 y 64 caracteres")
        if len(password) < 10:
            raise ValueError("La contrasena debe tener al menos 10 caracteres")
        if role not in {"admin", "operator"}:
            raise ValueError("Rol invalido")
