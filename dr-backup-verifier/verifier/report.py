"""Структуры результата проверки и их представление (текст / JSON)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    duration_sec: float = 0.0


@dataclass
class Report:
    run_id: str
    backup_file: str
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str = ""
    checks: list[CheckResult] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)

    def add(self, name: str, ok: bool, detail: str = "", duration: float = 0.0) -> CheckResult:
        c = CheckResult(name, ok, detail, round(duration, 2))
        self.checks.append(c)
        return c

    def finish(self) -> None:
        self.finished_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ok"] = self.ok
        return d

    def to_text(self) -> str:
        head = "ПРОЙДЕНА ✅" if self.ok else "ПРОВАЛЕНА ❌"
        lines = [f"=== Проверка бэкапа: {head} ===", f"Бэкап:  {self.backup_file}", f"Run ID: {self.run_id}", ""]
        for c in self.checks:
            mark = " OK " if c.ok else "FAIL"
            lines.append(f"[{mark}] {c.name:<34} {c.detail}  ({c.duration_sec:.1f}s)")
        if self.metrics:
            lines.append("")
            for k, v in self.metrics.items():
                lines.append(f"  {k}: {v}")
        for w in self.warnings:
            lines.append(f"  ⚠ {w}")
        return "\n".join(lines)

    def save(self, reports_dir: Path) -> Path:
        reports_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = reports_dir / f"verify-{stamp}-{self.run_id}.json"
        data = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        path.write_text(data, encoding="utf-8")
        (reports_dir / "latest.json").write_text(data, encoding="utf-8")
        return path
