"""Тонкая обёртка над subprocess: потоковая передача stdin/stdout из/в файлы (с поддержкой .gz).

Единственное место, где запускаются внешние процессы, поэтому в тестах его легко подменить.
"""
from __future__ import annotations

import gzip
import io
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

CHUNK = 1 << 20


@dataclass
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _feed(proc: subprocess.Popen, path: Path, errors: list) -> None:
    try:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rb") as f:
            shutil.copyfileobj(f, proc.stdin, CHUNK)
    except BrokenPipeError:
        pass  # процесс завершился раньше; причина будет в его returncode/stderr
    except Exception as exc:  # битый gzip, ошибка чтения файла и т.п.
        errors.append(f"ошибка чтения {path.name}: {exc!r}")
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass


class Runner:
    def run(
        self,
        args: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        stdin_path: str | Path | None = None,
        stdout_path: str | Path | None = None,
        timeout: float | None = None,
    ) -> Result:
        full_env = {**os.environ, **(env or {})}
        err = tempfile.TemporaryFile()
        sink: io.IOBase | None = None
        try:
            try:
                proc = subprocess.Popen(
                    list(args),
                    stdin=subprocess.PIPE if stdin_path else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=err,
                    env=full_env,
                )
            except FileNotFoundError:
                return Result(127, "", f"команда не найдена: {args[0]}")

            timed_out = threading.Event()

            def _kill() -> None:
                timed_out.set()
                proc.kill()

            timer = threading.Timer(timeout, _kill) if timeout else None
            if timer:
                timer.start()

            feed_errors: list[str] = []
            feeder = None
            if stdin_path:
                feeder = threading.Thread(
                    target=_feed, args=(proc, Path(stdin_path), feed_errors), daemon=True
                )
                feeder.start()

            if stdout_path:
                p = Path(stdout_path)
                sink = gzip.open(p, "wb") if p.suffix == ".gz" else open(p, "wb")
            else:
                sink = io.BytesIO()

            while True:
                chunk = proc.stdout.read(CHUNK)
                if not chunk:
                    break
                sink.write(chunk)
            proc.wait()
            if feeder:
                feeder.join()
            if timer:
                timer.cancel()

            err.seek(0)
            stderr = err.read().decode("utf-8", "replace")
            stdout = sink.getvalue().decode("utf-8", "replace") if isinstance(sink, io.BytesIO) else ""
            rc = proc.returncode
            if feed_errors:
                stderr += "\n" + "\n".join(feed_errors)
                rc = rc or 1
            if timed_out.is_set():
                stderr += f"\nпревышен таймаут {timeout}s"
                rc = rc or 124
            return Result(rc, stdout, stderr, timed_out.is_set())
        finally:
            if sink is not None:
                sink.close()
            err.close()
