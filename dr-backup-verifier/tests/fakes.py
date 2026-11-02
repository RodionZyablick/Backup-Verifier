"""Эмулятор docker + psql для юнит-тестов: никаких реальных процессов."""
from __future__ import annotations

from pathlib import Path

from verifier.runner import Result


class FakeEnv:
    """Имитация источника и тестового контейнера. Поведение настраивается полями."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.source_tables = {"public.users": 100, "public.orders": 500}
        self.restored_tables = dict(self.source_tables)
        self.columns = [
            ["public", "users", "1", "id", "integer"], ["public", "users", "2", "email", "text"],
            ["public", "orders", "1", "id", "integer"], ["public", "orders", "2", "amount", "numeric"],
        ]
        self.restored_columns = list(self.columns)
        self.restore_rc = 0
        self.list_rc = 0
        self.scan_rc = 0
        self.invalid_indexes = "0"
        self.container_alive = True
        self.containers: set[str] = set()
        self.networks: set[str] = set()
        self.ready_lines = 2
        self.rm_works = True
        self.custom: dict[str, str] = {}

    # --- интерфейс Runner ---
    def run(self, args, *, env=None, stdin_path=None, stdout_path=None, timeout=None) -> Result:
        args = list(args)
        self.calls.append(args)
        if args[0] == "docker":
            return self._docker(args, stdout_path)
        return self._client(args, stdout_path)

    # --- docker ---
    def _docker(self, a, stdout_path=None):
        sub = a[1]
        if sub == "info":
            return Result(0, "27.0\n")
        if sub == "image":
            return Result(0)
        if sub == "network" and a[2] == "create":
            self.networks.add(a[-1]); return Result(0, "id\n")
        if sub == "network" and a[2] == "rm":
            if self.rm_works: self.networks.discard(a[-1])
            return Result(0)
        if sub == "network" and a[2] == "ls":
            name = a[-1].replace("name=^", "").rstrip("$")
            return Result(0, "x\n" if name in self.networks else "")
        if sub == "run":
            self.containers.add(a[a.index("--name") + 1]); return Result(0, "cid\n")
        if sub == "inspect":
            return Result(0, "true\n" if self.container_alive else "false\n")
        if sub == "logs":
            return Result(0, "", "database system is ready to accept connections\n" * self.ready_lines)
        if sub == "rm":
            if self.rm_works: self.containers.discard(a[-1])
            return Result(0)
        if sub == "ps":
            name = a[-1].replace("name=^", "").rstrip("$")
            return Result(0, "x\n" if name in self.containers else "")
        if sub == "exec":
            return self._client(a, stdout_path)
        return Result(1, "", f"fake: неизвестная docker-команда {a}")

    # --- клиенты СУБД (внутри docker exec или напрямую) ---
    def _client(self, a, stdout_path):
        in_test = any(x.startswith("drv-") for x in a)
        if "pg_dump" in a and "--format=custom" in a:
            Path(stdout_path).write_bytes(b"PGDMP-fake-dump-content")
            return Result(0)
        if "pg_restore" in a and "--list" in a:
            return Result(self.list_rc, "; Archive\n1; table users\n2; table orders\n", "bad archive" if self.list_rc else "")
        if "pg_restore" in a:
            return Result(self.restore_rc, "", "pg_restore: error: could not execute query" if self.restore_rc else "")
        if "pg_dump" in a and "--file=/dev/null" in a:
            return Result(self.scan_rc, "", "invalid page in block" if self.scan_rc else "")
        if "psql" in a:
            return self._sql(a[a.index("-c") + 1], in_test)
        return Result(1, "", f"fake: неизвестная команда {a}")

    def _sql(self, sql, in_test):
        tables = self.restored_tables if in_test else self.source_tables
        cols = self.restored_columns if in_test else self.columns
        if sql == "SELECT 1":
            return Result(0, "1\n")
        if "FROM pg_tables" in sql:
            return Result(0, "".join(f"{t.split('.')[0]}\t{t.split('.')[1]}\n" for t in tables))
        if "information_schema.columns" in sql:
            return Result(0, "".join("\t".join(c) + "\n" for c in cols))
        if sql.startswith("SELECT COUNT(*) FROM"):
            t = sql.split("FROM ")[1].replace('"', "")
            return Result(0, f"{tables[t]}\n")
        if "indisvalid" in sql:
            return Result(0, self.invalid_indexes + "\n")
        if sql == "SELECT version()":
            return Result(0, "PostgreSQL 16.3\n")
        if sql in self.custom:
            return Result(0, self.custom[sql] + "\n")
        return Result(1, "", f"fake: неизвестный SQL {sql}")
