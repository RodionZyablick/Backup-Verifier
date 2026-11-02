"""Драйверы СУБД: как снять дамп, восстановить его, выполнить запрос и проверить целостность.

Секреты никогда не попадают в аргументы команд: они передаются через переменные окружения
(`docker exec -e VAR` без значения берёт его из окружения клиента).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Endpoint:
    """Куда подключаться: prefix + env задают «как добраться до клиентских утилит»."""
    prefix: list[str]                       # [] — клиенты на хосте; ["docker","exec","-i",...,name] — внутри контейнера
    env: dict[str, str]
    user: str
    database: str
    host: str | None = None                 # None — локальный сокет внутри контейнера
    port: int | None = None


def _q(s: str) -> str:
    return s.replace("'", "''")


class Driver:
    name: str
    default_image: str
    dump_suffix: str
    test_user: str
    default_tmpfs_path: str
    password_env_name: str
    default_port: int

    def exec_prefix(self, container: str, password: str | None) -> tuple[list[str], dict[str, str]]:
        prefix = ["docker", "exec", "-i"]
        env: dict[str, str] = {}
        if password:
            prefix += ["-e", self.password_env_name]
            env[self.password_env_name] = password
        return prefix + [container], env

    # -- команды, зависящие от СУБД --
    def dump_cmd(self, ep: Endpoint) -> list[str]: raise NotImplementedError
    def archive_list_cmd(self, ep: Endpoint) -> list[str] | None: return None
    def restore_cmd(self, ep: Endpoint) -> list[str]: raise NotImplementedError
    def query_cmd(self, ep: Endpoint, sql: str) -> list[str]: raise NotImplementedError
    def scan_cmd(self, ep: Endpoint) -> list[str]: raise NotImplementedError
    def container_env(self, password: str, database: str) -> tuple[list[str], dict[str, str]]: raise NotImplementedError
    def server_args(self) -> list[str]: return []
    def is_ready(self, logs: str) -> bool: raise NotImplementedError
    def quote_table(self, schema: str, table: str) -> str: raise NotImplementedError

    # -- SQL --
    def tables_sql(self, database: str) -> str: raise NotImplementedError
    def columns_sql(self, database: str) -> str: raise NotImplementedError
    version_sql = "SELECT version()"

    def engine_checks(self) -> list[tuple[str, str, str]]:
        """(название, SQL, ожидаемое значение) — проверки целостности, специфичные для СУБД."""
        return []


class PostgresDriver(Driver):
    name = "postgres"
    default_image = "postgres:16-alpine"
    dump_suffix = ".dump"
    test_user = "verifier"
    default_tmpfs_path = "/var/lib/postgresql/data"
    password_env_name = "PGPASSWORD"
    default_port = 5432

    def _conn(self, ep: Endpoint) -> list[str]:
        args = ["-U", ep.user]
        if ep.host:
            args += ["-h", ep.host, "-p", str(ep.port or self.default_port)]
        return args

    def dump_cmd(self, ep):
        return ep.prefix + ["pg_dump", "--format=custom", "--compress=6", *self._conn(ep), ep.database]

    def archive_list_cmd(self, ep):
        return ep.prefix + ["pg_restore", "--list"]

    def restore_cmd(self, ep):
        # --no-owner/--no-privileges: в тестовом контейнере нет ролей боевой БД
        return ep.prefix + ["pg_restore", "--no-owner", "--no-privileges", *self._conn(ep), "-d", ep.database]

    def query_cmd(self, ep, sql):
        return ep.prefix + ["psql", "-X", "-v", "ON_ERROR_STOP=1", *self._conn(ep), "-d", ep.database,
                            "-At", "-F", "\t", "-c", sql]

    def scan_cmd(self, ep):
        # Полное чтение всех данных: pg_dump упирается в любую битую страницу/TOAST
        return ep.prefix + ["pg_dump", "--format=plain", "--file=/dev/null", *self._conn(ep), ep.database]

    def container_env(self, password, database):
        return (
            ["-e", f"POSTGRES_USER={self.test_user}", "-e", "POSTGRES_PASSWORD", "-e", f"POSTGRES_DB={database}"],
            {"POSTGRES_PASSWORD": password},
        )

    def server_args(self):
        # Контейнер одноразовый: отключаем fsync ради скорости восстановления
        return ["-c", "fsync=off", "-c", "synchronous_commit=off", "-c", "full_page_writes=off"]

    def is_ready(self, logs):
        # Образ postgres сначала поднимает временный сервер для init, затем финальный
        return logs.count("database system is ready to accept connections") >= 2

    def quote_table(self, schema, table):
        return '"{}"."{}"'.format(schema.replace('"', '""'), table.replace('"', '""'))

    def tables_sql(self, database):
        return ("SELECT schemaname, tablename FROM pg_tables "
                "WHERE schemaname NOT IN ('pg_catalog','information_schema')")

    def columns_sql(self, database):
        return ("SELECT table_schema, table_name, ordinal_position, column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema NOT IN ('pg_catalog','information_schema')")

    def engine_checks(self):
        return [("нет невалидных индексов", "SELECT count(*) FROM pg_index WHERE NOT indisvalid", "0")]


class MySQLDriver(Driver):
    name = "mysql"
    default_image = "mysql:8.4"
    dump_suffix = ".sql.gz"
    test_user = "root"
    default_tmpfs_path = "/var/lib/mysql"
    password_env_name = "MYSQL_PWD"
    default_port = 3306

    def _conn(self, ep: Endpoint) -> list[str]:
        args = ["-u", ep.user]
        if ep.host:
            args += ["-h", ep.host, "-P", str(ep.port or self.default_port), "--protocol=tcp"]
        return args

    def dump_cmd(self, ep):
        return ep.prefix + ["mysqldump", "--single-transaction", "--routines", "--triggers", "--events",
                            *self._conn(ep), ep.database]

    def restore_cmd(self, ep):
        return ep.prefix + ["mysql", *self._conn(ep), ep.database]

    def query_cmd(self, ep, sql):
        return ep.prefix + ["mysql", *self._conn(ep), "-N", "-B", "-e", sql, ep.database]

    def scan_cmd(self, ep):
        return ep.prefix + ["mysqlcheck", *self._conn(ep), "--check", ep.database]

    def container_env(self, password, database):
        return (
            ["-e", "MYSQL_ROOT_PASSWORD", "-e", f"MYSQL_DATABASE={database}"],
            {"MYSQL_ROOT_PASSWORD": password},
        )

    def server_args(self):
        return ["--skip-log-bin", "--innodb-flush-log-at-trx-commit=0"]

    def is_ready(self, logs):
        # Временный сервер init слушает порт 0, финальный — 3306
        return re.search(r"ready for connections.*port:\s*3306", logs) is not None

    def quote_table(self, schema, table):
        return "`{}`.`{}`".format(schema.replace("`", "``"), table.replace("`", "``"))

    def tables_sql(self, database):
        return ("SELECT table_schema, table_name FROM information_schema.tables "
                f"WHERE table_schema='{_q(database)}' AND table_type='BASE TABLE'")

    def columns_sql(self, database):
        return ("SELECT table_schema, table_name, ordinal_position, column_name, column_type "
                f"FROM information_schema.columns WHERE table_schema='{_q(database)}'")


DRIVERS = {"postgres": PostgresDriver, "mysql": MySQLDriver}


def get_driver(kind: str) -> Driver:
    return DRIVERS[kind]()
