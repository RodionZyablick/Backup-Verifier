"""Обёртка над docker CLI. Все созданные ресурсы помечаются меткой dr-verifier=1."""
from __future__ import annotations

from typing import Mapping, Sequence

from .runner import Result, Runner

LABEL = "dr-verifier"


class Docker:
    def __init__(self, runner: Runner):
        self.runner = runner

    def _run(self, *args: str, **kw) -> Result:
        return self.runner.run(["docker", *args], **kw)

    def available(self) -> Result:
        return self._run("info", "--format", "{{.ServerVersion}}", timeout=30)

    def image_exists(self, image: str) -> bool:
        return self._run("image", "inspect", image, timeout=30).ok

    def pull(self, image: str) -> Result:
        return self._run("pull", image, timeout=1800)

    def create_network(self, name: str, run_id: str) -> Result:
        # --internal: у контейнеров сети нет маршрута наружу — полная изоляция
        return self._run(
            "network", "create", "--internal",
            "--label", f"{LABEL}=1", "--label", f"{LABEL}.run={run_id}", name,
            timeout=60,
        )

    def run_detached(
        self,
        *,
        name: str,
        image: str,
        network: str,
        run_id: str,
        memory: str,
        cpus: str,
        pids_limit: int,
        tmpfs: str | None,
        env_flags: Sequence[str],
        env: Mapping[str, str],
        command_args: Sequence[str],
    ) -> Result:
        args = [
            "run", "-d", "--name", name, "--network", network,
            "--label", f"{LABEL}=1", "--label", f"{LABEL}.run={run_id}",
            "--memory", memory, "--cpus", cpus, "--pids-limit", str(pids_limit),
        ]
        if tmpfs:
            args += ["--tmpfs", tmpfs]
        args += [*env_flags, image, *command_args]
        return self._run(*args, env=env, timeout=300)

    def is_running(self, name: str) -> bool:
        r = self._run("inspect", "-f", "{{.State.Running}}", name, timeout=30)
        return r.ok and r.stdout.strip() == "true"

    def logs(self, name: str) -> str:
        r = self._run("logs", name, timeout=60)
        return (r.stdout or "") + (r.stderr or "")

    def remove_container(self, name: str) -> Result:
        # -v удаляет и анонимные тома — на диске не остаётся копии данных
        return self._run("rm", "-f", "-v", name, timeout=120)

    def remove_network(self, name: str) -> Result:
        return self._run("network", "rm", name, timeout=60)

    def container_exists(self, name: str) -> bool:
        r = self._run("ps", "-aq", "--filter", f"name=^{name}$", timeout=30)
        return bool(r.stdout.strip())

    def network_exists(self, name: str) -> bool:
        r = self._run("network", "ls", "-q", "--filter", f"name=^{name}$", timeout=30)
        return bool(r.stdout.strip())

    def labeled_containers(self) -> list[str]:
        r = self._run("ps", "-aq", "--filter", f"label={LABEL}=1", timeout=30)
        return r.stdout.split()

    def labeled_networks(self) -> list[str]:
        r = self._run("network", "ls", "-q", "--filter", f"label={LABEL}=1", timeout=30)
        return r.stdout.split()
