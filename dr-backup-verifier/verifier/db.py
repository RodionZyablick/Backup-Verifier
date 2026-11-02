"""Выполнение запросов через клиентские утилиты СУБД и сбор «отпечатка» базы."""
from __future__ import annotations

import hashlib

from .drivers import Driver, Endpoint
from .runner import Runner


class QueryError(Exception):
    pass


class Db:
    def __init__(self, runner: Runner, driver: Driver, endpoint: Endpoint, timeout: int = 600):
        self.runner, self.driver, self.ep, self.timeout = runner, driver, endpoint, timeout

    def query(self, sql: str) -> list[list[str]]:
        r = self.runner.run(self.driver.query_cmd(self.ep, sql), env=self.ep.env, timeout=self.timeout)
        if not r.ok:
            raise QueryError(f"запрос не выполнен ({sql[:80]!r}): {r.stderr.strip()[-400:]}")
        return [line.split("\t") for line in r.stdout.splitlines() if line != ""]

    def scalar(self, sql: str) -> str:
        rows = self.query(sql)
        if not rows or not rows[0]:
            raise QueryError(f"запрос не вернул значения: {sql[:80]!r}")
        return rows[0][0]

    def tables(self) -> list[tuple[str, str]]:
        rows = self.query(self.driver.tables_sql(self.ep.database))
        return sorted((r[0], r[1]) for r in rows)

    def row_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for schema, table in self.tables():
            q = self.driver.quote_table(schema, table)
            counts[f"{schema}.{table}"] = int(self.scalar(f"SELECT COUNT(*) FROM {q}"))
        return counts

    def schema_columns(self) -> list[str]:
        """Каноничный список колонок. Сортировка в Python, а не в SQL, чтобы не зависеть от collation."""
        rows = self.query(self.driver.columns_sql(self.ep.database))
        rows = sorted(rows, key=lambda r: (r[0], r[1], int(r[2])))
        return [f"{r[0]}.{r[1]}.{r[3]}:{r[4]}" for r in rows]

    def version(self) -> str:
        return self.scalar(self.driver.version_sql)


def schema_hash(columns: list[str]) -> str:
    return hashlib.sha256("\n".join(columns).encode()).hexdigest()
