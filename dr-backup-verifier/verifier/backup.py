"""Создание бэкапа, манифест, список бэкапов и ротация."""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .db import Db, schema_hash
from .drivers import Driver, Endpoint
from .runner import Runner

log = logging.getLogger("dr.backup")


class BackupError(Exception):
    pass


@dataclass
class Manifest:
    created_at: str
    db_type: str
    database: str
    server_version: str
    file: str
    size_bytes: int
    sha256: str
    schema_hash: str
    schema_columns: list[str]
    row_counts: dict[str, int]
    last_verification: dict | None = None


@dataclass
class BackupInfo:
    path: Path
    manifest: Manifest

    @property
    def manifest_path(self) -> Path:
        return manifest_path_for(self.path)


def manifest_path_for(dump: Path) -> Path:
    return dump.with_name(dump.name + ".manifest.json")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def save_manifest(info: BackupInfo) -> None:
    info.manifest_path.write_text(json.dumps(asdict(info.manifest), ensure_ascii=False, indent=2), encoding="utf-8")


def load_backup(dump: Path) -> BackupInfo:
    mp = manifest_path_for(dump)
    if not dump.exists():
        raise BackupError(f"Файл бэкапа не найден: {dump}")
    if not mp.exists():
        raise BackupError(f"Нет манифеста {mp.name}: без него нечего сверять после восстановления")
    return BackupInfo(dump, Manifest(**json.loads(mp.read_text(encoding="utf-8"))))


def list_backups(backup_dir: Path) -> list[BackupInfo]:
    items = []
    for mp in backup_dir.glob("*.manifest.json"):
        dump = mp.with_name(mp.name[: -len(".manifest.json")])
        try:
            items.append(load_backup(dump))
        except (BackupError, TypeError, json.JSONDecodeError) as exc:
            log.warning("Пропускаю %s: %s", mp.name, exc)
    return sorted(items, key=lambda i: i.manifest.created_at)


def source_endpoint(cfg: Config, driver: Driver) -> Endpoint:
    s = cfg.source
    if s.container:
        prefix, env = driver.exec_prefix(s.container, s.password)
        return Endpoint(prefix, env, s.user, s.database)
    env = {driver.password_env_name: s.password} if s.password else {}
    return Endpoint([], env, s.user, s.database, host=s.host, port=s.port)


def create_backup(cfg: Config, runner: Runner, driver: Driver) -> BackupInfo:
    ep = source_endpoint(cfg, driver)
    cfg.backup_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    final = cfg.backup_dir / f"{cfg.name}-{cfg.source.database}-{stamp}{driver.dump_suffix}"
    part = final.with_name(".partial-" + final.name)  # расширение сохраняется (.gz → сжатие в Runner)

    log.info("Снимаю дамп %s → %s", cfg.source.database, final.name)
    res = runner.run(driver.dump_cmd(ep), env=ep.env, stdout_path=part, timeout=cfg.dump_timeout_sec)
    if not res.ok:
        part.unlink(missing_ok=True)
        raise BackupError(f"дамп завершился с кодом {res.returncode}: {res.stderr.strip()[-600:]}")
    if not part.exists() or part.stat().st_size == 0:
        part.unlink(missing_ok=True)
        raise BackupError("дамп пустой")
    part.rename(final)

    log.info("Собираю метаданные источника (схема, число строк)")
    db = Db(runner, driver, ep)
    columns = db.schema_columns()
    manifest = Manifest(
        created_at=now.isoformat(), db_type=driver.name, database=cfg.source.database,
        server_version=db.version(), file=final.name, size_bytes=final.stat().st_size,
        sha256=sha256_file(final), schema_hash=schema_hash(columns),
        schema_columns=columns, row_counts=db.row_counts(),
    )
    info = BackupInfo(final, manifest)
    save_manifest(info)
    log.info("Бэкап готов: %s (%.1f MiB, %d таблиц)", final.name, manifest.size_bytes / 2**20, len(manifest.row_counts))
    return info


def apply_retention(backup_dir: Path, keep: int) -> list[str]:
    """Оставляет `keep` последних бэкапов; последний успешно проверенный не удаляется никогда."""
    items = list_backups(backup_dir)
    if keep <= 0 or len(items) <= keep:
        return []
    protected = next(
        (i for i in reversed(items) if i.manifest.last_verification and i.manifest.last_verification.get("ok")),
        None,
    )
    removed = []
    for info in items[:-keep]:
        if protected and info.path == protected.path:
            continue
        info.path.unlink(missing_ok=True)
        info.manifest_path.unlink(missing_ok=True)
        removed.append(info.path.name)
    if removed:
        log.info("Ротация: удалено %d старых бэкапов", len(removed))
    return removed
