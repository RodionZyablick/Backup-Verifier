"""Загрузка и валидация конфигурации (TOML, только стандартная библиотека)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    pass


@dataclass
class SourceConfig:
    type: str
    database: str
    user: str
    container: str | None = None   # если задан — дамп снимается через `docker exec` в этом контейнере
    host: str = "localhost"        # иначе используются клиенты pg_dump/mysqldump на хосте
    port: int | None = None
    password_env: str | None = None

    @property
    def password(self) -> str | None:
        return os.environ.get(self.password_env) if self.password_env else None


@dataclass
class CustomCheck:
    name: str
    sql: str
    expect_equals: str | None = None
    expect_min: float | None = None
    expect_max: float | None = None


@dataclass
class VerifyConfig:
    image: str | None = None
    pull_if_missing: bool = True
    startup_timeout_sec: int = 180
    restore_timeout_sec: int = 3600
    memory: str = "1g"
    cpus: str = "1.0"
    pids_limit: int = 1024
    use_tmpfs: bool = False
    tmpfs_size: str = "2g"
    tmpfs_path: str | None = None
    row_count_tolerance_percent: float = 0.0
    row_count_tolerance_abs: int = 0
    rto_target_sec: int | None = None
    rpo_target_hours: float | None = None
    custom_checks: list[CustomCheck] = field(default_factory=list)


@dataclass
class NotifyConfig:
    webhook_url_env: str | None = None
    telegram_token_env: str | None = None
    telegram_chat_id_env: str | None = None
    on_success: bool = False


@dataclass
class Config:
    name: str
    backup_dir: Path
    reports_dir: Path
    retention_keep: int
    source: SourceConfig
    verify: VerifyConfig
    notify: NotifyConfig
    dump_timeout_sec: int = 3600


def load_config(path: str | Path) -> Config:
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"Конфиг не найден: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Ошибка синтаксиса TOML в {path}: {exc}") from None

    base = path.resolve().parent
    proj = raw.get("project", {})
    src = raw.get("source")
    if not src:
        raise ConfigError("Отсутствует секция [source]")
    for key in ("type", "database", "user"):
        if not src.get(key):
            raise ConfigError(f"[source] обязательное поле '{key}' не задано")
    if src["type"] not in ("postgres", "mysql"):
        raise ConfigError(f"[source].type должен быть 'postgres' или 'mysql', получено '{src['type']}'")

    source = SourceConfig(
        type=src["type"], database=src["database"], user=src["user"],
        container=src.get("container"), host=src.get("host", "localhost"),
        port=src.get("port"), password_env=src.get("password_env"),
    )

    v = raw.get("verify", {})
    checks = []
    for i, c in enumerate(v.get("custom_checks", []), 1):
        if "sql" not in c:
            raise ConfigError(f"[[verify.custom_checks]] #{i}: нужен ключ 'sql'")
        if not any(k in c for k in ("expect_equals", "expect_min", "expect_max")):
            raise ConfigError(f"[[verify.custom_checks]] #{i}: задайте expect_equals / expect_min / expect_max")
        checks.append(CustomCheck(
            name=c.get("name", f"custom_check_{i}"), sql=c["sql"],
            expect_equals=None if "expect_equals" not in c else str(c["expect_equals"]),
            expect_min=c.get("expect_min"), expect_max=c.get("expect_max"),
        ))

    known = {f for f in VerifyConfig.__dataclass_fields__} - {"custom_checks"}
    unknown = set(v) - known - {"custom_checks"}
    if unknown:
        raise ConfigError(f"[verify] неизвестные ключи: {', '.join(sorted(unknown))}")
    verify = VerifyConfig(**{k: v[k] for k in v if k in known}, custom_checks=checks)

    n = raw.get("notify", {})
    notify = NotifyConfig(**{k: n[k] for k in n if k in NotifyConfig.__dataclass_fields__})

    return Config(
        name=proj.get("name", "db"),
        backup_dir=(base / proj.get("backup_dir", "./backups")).resolve(),
        reports_dir=(base / proj.get("reports_dir", "./reports")).resolve(),
        retention_keep=int(proj.get("retention_keep", 7)),
        dump_timeout_sec=int(proj.get("dump_timeout_sec", 3600)),
        source=source, verify=verify, notify=notify,
    )
