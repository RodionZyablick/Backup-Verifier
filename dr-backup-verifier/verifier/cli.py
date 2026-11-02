"""Командная строка: backup | verify | run | list | cleanup."""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
from pathlib import Path

from .backup import BackupError, apply_retention, create_backup, list_backups, load_backup, save_manifest
from .config import ConfigError, load_config
from .docker import Docker
from .drivers import get_driver
from .notify import notify
from .runner import Runner
from .verify import Verifier

EXIT_OK, EXIT_VERIFY_FAILED, EXIT_ERROR = 0, 1, 2
log = logging.getLogger("dr")


def _sigterm(_signo, _frame):
    raise KeyboardInterrupt  # чтобы сработал finally с удалением тестового контейнера


def _pick_backup(cfg, arg: str | None):
    if arg and arg != "latest":
        return load_backup(Path(arg))
    items = list_backups(cfg.backup_dir)
    if not items:
        raise BackupError(f"в {cfg.backup_dir} нет бэкапов")
    return items[-1]


def _verify_and_report(cfg, docker, driver, runner, info, args) -> int:
    v = Verifier(cfg, docker, driver, runner,
                 keep_on_failure=getattr(args, "keep_on_failure", False),
                 skip_checksum=getattr(args, "skip_checksum", False))
    report = v.verify(info)
    path = report.save(cfg.reports_dir)
    info.manifest.last_verification = {
        "ok": report.ok, "at": report.finished_at, "report": path.name,
        "restore_sec": report.metrics.get("restore_sec (измеренный RTO)"),
    }
    save_manifest(info)
    print(report.to_text())
    print(f"\nОтчёт: {path}")
    notify(cfg.notify, report)
    return EXIT_OK if report.ok else EXIT_VERIFY_FAILED


def cmd_cleanup(docker: Docker) -> int:
    containers, networks = docker.labeled_containers(), docker.labeled_networks()
    for c in containers:
        docker.remove_container(c)
    for n in networks:
        docker.remove_network(n)
    print(f"Удалено контейнеров: {len(containers)}, сетей: {len(networks)}")
    return EXIT_OK


def cmd_list(cfg) -> int:
    items = list_backups(cfg.backup_dir)
    if not items:
        print("Бэкапов нет")
        return EXIT_OK
    print(f"{'ФАЙЛ':<52} {'РАЗМЕР':>9}  ПРОВЕРКА")
    for i in items:
        lv = i.manifest.last_verification
        status = "не проверялся" if not lv else ("✅ " if lv["ok"] else "❌ ") + lv["at"][:19]
        print(f"{i.path.name:<52} {i.manifest.size_bytes / 2**20:>7.1f}Mi  {status}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dr-verify", description="Автоматическая проверка резервных копий БД")
    p.add_argument("-c", "--config", default="config.toml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backup", help="только снять бэкап")
    for name, h in (("verify", "проверить существующий бэкап"), ("run", "снять бэкап и сразу проверить")):
        s = sub.add_parser(name, help=h)
        s.add_argument("--keep-on-failure", action="store_true", help="не удалять контейнер при провале (отладка)")
        if name == "verify":
            s.add_argument("--backup", default="latest", help="путь к дампу или 'latest'")
            s.add_argument("--skip-checksum", action="store_true", help="пропустить SHA-256 (демо: пусть ловит restore)")
    sub.add_parser("list", help="список бэкапов и статус проверки")
    sub.add_parser("cleanup", help="удалить «осиротевшие» тестовые контейнеры и сети")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    signal.signal(signal.SIGTERM, _sigterm)
    runner = Runner()
    docker = Docker(runner)
    try:
        if args.cmd == "cleanup":
            return cmd_cleanup(docker)
        cfg = load_config(args.config)
        driver = get_driver(cfg.source.type)
        if args.cmd == "list":
            return cmd_list(cfg)
        if args.cmd in ("verify", "run"):
            probe = docker.available()
            if not probe.ok:
                raise RuntimeError(f"Docker недоступен: {probe.stderr.strip() or 'daemon не отвечает'}")
        if args.cmd == "backup":
            create_backup(cfg, runner, driver)
            apply_retention(cfg.backup_dir, cfg.retention_keep)
            return EXIT_OK
        if args.cmd == "verify":
            return _verify_and_report(cfg, docker, driver, runner, _pick_backup(cfg, args.backup), args)
        if args.cmd == "run":
            info = create_backup(cfg, runner, driver)
            code = _verify_and_report(cfg, docker, driver, runner, info, args)
            apply_retention(cfg.backup_dir, cfg.retention_keep)
            return code
    except (ConfigError, BackupError, RuntimeError) as exc:
        log.error("%s", exc)
        return EXIT_ERROR
    except KeyboardInterrupt:
        log.error("Прервано; тестовые ресурсы удалены")
        return EXIT_ERROR
    return EXIT_ERROR
