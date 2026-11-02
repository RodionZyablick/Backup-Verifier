"""Ядро проекта: восстановление бэкапа в изолированном временном контейнере и проверка данных."""
from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Callable

from .backup import BackupInfo, sha256_file
from .config import Config, CustomCheck
from .db import Db, QueryError, schema_hash
from .docker import Docker
from .drivers import Driver, Endpoint
from .report import Report
from .runner import Runner

log = logging.getLogger("dr.verify")
Step = Callable[[], tuple[bool, str]]


def evaluate_custom(check: CustomCheck, value: str) -> tuple[bool, str]:
    """Сравнивает результат SQL с ожиданиями из конфига."""
    problems = []
    if check.expect_equals is not None:
        try:
            same = float(value) == float(check.expect_equals)
        except ValueError:
            same = value == check.expect_equals
        if not same:
            problems.append(f"ожидалось = {check.expect_equals}")
    if check.expect_min is not None or check.expect_max is not None:
        try:
            num = float(value)
            if check.expect_min is not None and num < check.expect_min:
                problems.append(f"ожидалось ≥ {check.expect_min}")
            if check.expect_max is not None and num > check.expect_max:
                problems.append(f"ожидалось ≤ {check.expect_max}")
        except ValueError:
            problems.append("значение не число")
    return (not problems), f"результат={value}" + (f" ({'; '.join(problems)})" if problems else "")


def compare_row_counts(expected: dict[str, int], actual: dict[str, int], pct: float, abs_tol: int) -> list[str]:
    problems = []
    for t in sorted(set(expected) - set(actual)):
        problems.append(f"таблица {t} отсутствует после восстановления")
    for t in sorted(set(actual) - set(expected)):
        problems.append(f"лишняя таблица {t}")
    for t in sorted(set(expected) & set(actual)):
        e, a = expected[t], actual[t]
        allowed = max(abs_tol, int(e * pct / 100))
        if abs(e - a) > allowed:
            problems.append(f"{t}: в бэкапе-манифесте {e}, восстановлено {a} (допуск ±{allowed})")
    return problems


class Verifier:
    def __init__(
        self, cfg: Config, docker: Docker, driver: Driver, runner: Runner, *,
        keep_on_failure: bool = False, skip_checksum: bool = False,
        sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
    ):
        self.cfg, self.docker, self.driver, self.runner = cfg, docker, driver, runner
        self.keep_on_failure, self.skip_checksum = keep_on_failure, skip_checksum
        self.sleep, self.clock = sleep, clock
        self.run_id = datetime.now(timezone.utc).strftime("%H%M%S") + secrets.token_hex(2)
        self.container = f"drv-{self.run_id}"
        self.network = f"drv-net-{self.run_id}"
        self._password = secrets.token_urlsafe(18)   # одноразовый пароль тестовой БД
        self._created_container = False
        self._created_network = False
        self._db: Db | None = None
        self._ep: Endpoint | None = None

    # ------------------------------------------------------------------ публичный API
    def verify(self, info: BackupInfo) -> Report:
        report = Report(self.run_id, info.path.name)
        try:
            self._run(info, report)
        except Exception as exc:  # непредвиденная ошибка тоже должна дать отчёт, а не трейсбек
            log.exception("Непредвиденная ошибка")
            report.add("unexpected_error", False, repr(exc))
        finally:
            self._teardown(report)
        report.finish()
        return report

    # ------------------------------------------------------------------ шаги
    def _step(self, report: Report, name: str, fn: Step) -> bool:
        t0 = self.clock()
        log.info("→ %s", name)
        try:
            ok, detail = fn()
        except QueryError as exc:
            ok, detail = False, str(exc)
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        report.add(name, ok, detail, self.clock() - t0)
        if not ok:
            log.error("✗ %s: %s", name, detail)
        return ok

    def _run(self, info: BackupInfo, report: Report) -> None:
        cfg, m = self.cfg.verify, info.manifest
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(m.created_at)).total_seconds() / 3600
        report.metrics["backup_age_hours"] = round(age_h, 2)

        if not self._step(report, "backup_file_integrity", lambda: self._check_file(info)):
            return
        if cfg.rpo_target_hours is not None:
            self._step(report, "rpo_target", lambda: (
                age_h <= cfg.rpo_target_hours,
                f"возраст бэкапа {age_h:.1f} ч, цель RPO ≤ {cfg.rpo_target_hours} ч"))
        if not self._step(report, "image_available", self._ensure_image):
            return

        t_prov = self.clock()
        if not self._step(report, "provision_isolated_container", self._provision):
            return
        report.metrics["provision_sec"] = round(self.clock() - t_prov, 1)

        if not self._step(report, "archive_readable", lambda: self._check_archive(info)):
            return

        t_rest = self.clock()
        restored = self._step(report, "restore", lambda: self._restore(info))
        restore_sec = self.clock() - t_rest
        report.metrics["restore_sec (измеренный RTO)"] = round(restore_sec, 1)
        if not restored:
            return
        if cfg.rto_target_sec is not None:
            self._step(report, "rto_target", lambda: (
                restore_sec <= cfg.rto_target_sec,
                f"восстановление заняло {restore_sec:.0f}s, цель RTO ≤ {cfg.rto_target_sec}s"))

        self._step(report, "schema_matches_source", lambda: self._check_schema(m))
        self._step(report, "row_counts_match", lambda: self._check_rows(m, report))
        for name, sql, expected in self.driver.engine_checks():
            chk = CustomCheck(name, sql, expect_equals=expected)
            self._step(report, name, lambda c=chk: evaluate_custom(c, self._db.scalar(c.sql)))
        self._step(report, "full_data_scan", self._full_scan)
        for chk in cfg.custom_checks:
            self._step(report, f"custom: {chk.name}", lambda c=chk: evaluate_custom(c, self._db.scalar(c.sql)))

    def _check_file(self, info: BackupInfo) -> tuple[bool, str]:
        p, m = info.path, info.manifest
        if not p.exists():
            return False, "файл бэкапа не найден"
        size = p.stat().st_size
        if size == 0:
            return False, "файл бэкапа пустой"
        if self.skip_checksum:
            return True, f"контрольная сумма ПРОПУЩЕНА (--skip-checksum), {size} байт"
        if size != m.size_bytes:
            return False, f"размер {size} ≠ {m.size_bytes} по манифесту"
        actual = sha256_file(p)
        if actual != m.sha256:
            return False, f"SHA-256 не совпадает: {actual[:12]}… ≠ {m.sha256[:12]}…"
        return True, f"SHA-256 совпадает, {size / 2**20:.2f} MiB"

    def _ensure_image(self) -> tuple[bool, str]:
        image = self.cfg.verify.image or self.driver.default_image
        if self.docker.image_exists(image):
            return True, image
        if not self.cfg.verify.pull_if_missing:
            return False, f"образ {image} отсутствует локально, pull_if_missing=false"
        r = self.docker.pull(image)
        return r.ok, f"{image} загружен" if r.ok else f"не удалось загрузить {image}: {r.stderr.strip()[-300:]}"

    def _provision(self) -> tuple[bool, str]:
        v, d = self.cfg.verify, self.driver
        image = v.image or d.default_image
        r = self.docker.create_network(self.network, self.run_id)
        if not r.ok:
            return False, f"не создана изолированная сеть: {r.stderr.strip()[-300:]}"
        self._created_network = True

        env_flags, env = d.container_env(self._password, self.cfg.source.database)
        tmpfs = f"{v.tmpfs_path or d.default_tmpfs_path}:rw,size={v.tmpfs_size}" if v.use_tmpfs else None
        r = self.docker.run_detached(
            name=self.container, image=image, network=self.network, run_id=self.run_id,
            memory=v.memory, cpus=v.cpus, pids_limit=v.pids_limit, tmpfs=tmpfs,
            env_flags=env_flags, env=env, command_args=d.server_args(),
        )
        if not r.ok:
            return False, f"контейнер не запущен: {r.stderr.strip()[-300:]}"
        self._created_container = True

        prefix, penv = d.exec_prefix(self.container, self._password if d.name == "mysql" else None)
        self._ep = Endpoint(prefix, penv, d.test_user, self.cfg.source.database)
        self._db = Db(self.runner, d, self._ep)

        deadline = self.clock() + v.startup_timeout_sec
        while self.clock() < deadline:
            if not self.docker.is_running(self.container):
                tail = self.docker.logs(self.container)[-500:]
                return False, f"контейнер остановился при старте: {tail}"
            if d.is_ready(self.docker.logs(self.container)):
                try:
                    self._db.scalar("SELECT 1")
                    return True, f"{image}, сеть --internal (без выхода наружу), порты не публикуются"
                except QueryError:
                    pass
            self.sleep(1)
        return False, f"СУБД не стала доступна за {v.startup_timeout_sec}s"

    def _check_archive(self, info: BackupInfo) -> tuple[bool, str]:
        cmd = self.driver.archive_list_cmd(self._ep)
        if cmd is None:   # mysql: .sql.gz — целостность проверяется потоковой распаковкой при restore
            return True, "структура проверяется при восстановлении (gzip-поток)"
        r = self.runner.run(cmd, env=self._ep.env, stdin_path=info.path, timeout=600)
        if not r.ok:
            return False, f"архив нечитаем: {r.stderr.strip()[-400:]}"
        entries = sum(1 for ln in r.stdout.splitlines() if ln and not ln.startswith(";"))
        return True, f"оглавление архива читается, {entries} объектов"

    def _restore(self, info: BackupInfo) -> tuple[bool, str]:
        r = self.runner.run(
            self.driver.restore_cmd(self._ep), env=self._ep.env, stdin_path=info.path,
            timeout=self.cfg.verify.restore_timeout_sec,
        )
        if not r.ok:
            return False, f"восстановление упало (код {r.returncode}): {r.stderr.strip()[-600:]}"
        return True, "дамп восстановлен без ошибок"

    def _check_schema(self, m) -> tuple[bool, str]:
        cols = self._db.schema_columns()
        if schema_hash(cols) == m.schema_hash:
            return True, f"{len(cols)} колонок, отпечаток схемы совпадает"
        missing, extra = sorted(set(m.schema_columns) - set(cols)), sorted(set(cols) - set(m.schema_columns))
        return False, f"схема отличается; нет: {missing[:5]}; лишнее: {extra[:5]}"

    def _check_rows(self, m, report: Report) -> tuple[bool, str]:
        actual = self._db.row_counts()
        v = self.cfg.verify
        problems = compare_row_counts(m.row_counts, actual, v.row_count_tolerance_percent, v.row_count_tolerance_abs)
        report.metrics["tables_checked"] = len(actual)
        report.metrics["rows_restored"] = sum(actual.values())
        if problems:
            return False, "; ".join(problems[:6]) + (f" … (+{len(problems) - 6})" if len(problems) > 6 else "")
        return True, f"{len(actual)} таблиц, {sum(actual.values())} строк — совпадает с источником"

    def _full_scan(self) -> tuple[bool, str]:
        r = self.runner.run(self.driver.scan_cmd(self._ep), env=self._ep.env, timeout=self.cfg.verify.restore_timeout_sec)
        if not r.ok:
            return False, f"чтение всех данных завершилось ошибкой: {r.stderr.strip()[-400:]}"
        return True, "все данные читаются без ошибок"

    # ------------------------------------------------------------------ уборка
    def _teardown(self, report: Report) -> None:
        if self.keep_on_failure and not report.ok and (self._created_container or self._created_network):
            report.warnings.append(
                f"--keep-on-failure: контейнер {self.container} и сеть {self.network} оставлены для отладки; "
                f"удалить: python -m verifier cleanup")
            return
        problems = []
        if self._created_container:
            self.docker.remove_container(self.container)
            if self.docker.container_exists(self.container):
                problems.append(f"контейнер {self.container} не удалён")
        if self._created_network:
            self.docker.remove_network(self.network)
            if self.docker.network_exists(self.network):
                problems.append(f"сеть {self.network} не удалена")
        if self._created_container or self._created_network:
            report.add("cleanup", not problems, "; ".join(problems) or "тестовые контейнер, сеть и данные удалены")
