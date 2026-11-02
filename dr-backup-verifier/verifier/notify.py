"""Уведомления: универсальный webhook (Slack/Mattermost/Discord-совместимый) и Telegram."""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request

from .config import NotifyConfig
from .report import Report

log = logging.getLogger("dr.notify")


def _post(url: str, data: bytes, headers: dict[str, str]) -> None:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15):
        pass


def summary(report: Report) -> str:
    status = "✅ бэкап восстанавливается" if report.ok else "❌ БЭКАП НЕ ПРОШЁЛ ПРОВЕРКУ"
    failed = [f"• {c.name}: {c.detail}" for c in report.checks if not c.ok]
    rto = report.metrics.get("restore_sec (измеренный RTO)")
    parts = [f"{status}", f"Файл: {report.backup_file}"]
    if rto is not None:
        parts.append(f"Измеренный RTO: {rto}s")
    return "\n".join(parts + failed)


def notify(cfg: NotifyConfig, report: Report) -> None:
    if report.ok and not cfg.on_success:
        return
    text = summary(report)
    if cfg.webhook_url_env and (url := os.environ.get(cfg.webhook_url_env)):
        try:
            _post(url, json.dumps({"text": text, "content": text}).encode(), {"Content-Type": "application/json"})
        except Exception as exc:
            log.error("Webhook не отправлен: %s", exc)
    token = os.environ.get(cfg.telegram_token_env or "")
    chat = os.environ.get(cfg.telegram_chat_id_env or "")
    if token and chat:
        try:
            body = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
            _post(f"https://api.telegram.org/bot{token}/sendMessage", body,
                  {"Content-Type": "application/x-www-form-urlencoded"})
        except Exception as exc:
            log.error("Telegram не отправлен: %s", exc)
