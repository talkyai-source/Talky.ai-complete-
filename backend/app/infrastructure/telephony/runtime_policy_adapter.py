"""
WS-G runtime adapter for OpenSIPS and FreeSWITCH policy activation.

Uses command execution hooks with a simulation mode by default, so local/dev
environments can exercise the orchestration safely without requiring direct
runtime shell access.
"""

from __future__ import annotations

import asyncio
import os
import shlex
from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class RuntimeAdapterConfig:
    mode: str = "simulate"
    timeout_seconds: float = 10.0
    opensips_reload_command: str = "opensips-cli -x mi ds_reload"
    opensips_verify_command: str = "opensips-cli -x mi ds_list 1"
    freeswitch_reload_command: str = "fs_cli -x reloadxml"
    freeswitch_verify_command: str = "fs_cli -x status"

    @classmethod
    def from_env(cls) -> "RuntimeAdapterConfig":
        timeout_raw = os.getenv("TELEPHONY_RUNTIME_COMMAND_TIMEOUT_SECONDS", "10")
        try:
            timeout_seconds = float(timeout_raw)
        except ValueError:
            timeout_seconds = 10.0

        return cls(
            mode=os.getenv("TELEPHONY_RUNTIME_APPLY_MODE", "simulate").strip().lower(),
            timeout_seconds=max(1.0, timeout_seconds),
            opensips_reload_command=os.getenv(
                "OPENSIPS_DISPATCHER_RELOAD_COMMAND",
                os.getenv(
                    "KAMAILIO_DISPATCHER_RELOAD_COMMAND",
                    "opensips-cli -x mi ds_reload",
                ),
            ),
            opensips_verify_command=os.getenv(
                "OPENSIPS_DISPATCHER_VERIFY_COMMAND",
                os.getenv(
                    "KAMAILIO_DISPATCHER_VERIFY_COMMAND",
                    "opensips-cli -x mi ds_list 1",
                ),
            ),
            freeswitch_reload_command=os.getenv(
                "FREESWITCH_RELOADXML_COMMAND",
                "fs_cli -x reloadxml",
            ),
            freeswitch_verify_command=os.getenv(
                "FREESWITCH_VERIFY_COMMAND",
                "fs_cli -x status",
            ),
        )


class RuntimeCommandError(RuntimeError):
    def __init__(self, *, stage: str, command: str, stdout: str, stderr: str, returncode: int):
        super().__init__(f"Runtime command failed at {stage}: {command}")
        self.stage = stage
        self.command = command
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage,
            "command": self.command,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


class RuntimePolicyAdapter:
    def __init__(self, config: RuntimeAdapterConfig | None = None):
        self.config = config or RuntimeAdapterConfig.from_env()

    async def apply(self, artifact: Dict[str, Any]) -> Dict[str, Any]:
        del artifact  # Runtime currently applies via DB-backed reload/requery commands.
        opensips = await self._run_stage(
            stage="opensips_apply",
            command=self.config.opensips_reload_command,
        )
        return {
            "opensips": opensips,
            "kamailio": opensips,  # Backward-compatible alias for existing API contracts.
            "freeswitch": await self._run_stage(
                stage="freeswitch_apply",
                command=self.config.freeswitch_reload_command,
            ),
        }

    async def verify(self, artifact: Dict[str, Any]) -> Dict[str, Any]:
        del artifact
        opensips = await self._run_stage(
            stage="opensips_verify",
            command=self.config.opensips_verify_command,
        )
        return {
            "opensips": opensips,
            "kamailio": opensips,  # Backward-compatible alias for existing API contracts.
            "freeswitch": await self._run_stage(
                stage="freeswitch_verify",
                command=self.config.freeswitch_verify_command,
            ),
        }

    async def _run_stage(self, *, stage: str, command: str) -> Dict[str, Any]:
        if self.config.mode == "simulate":
            return {
                "mode": "simulate",
                "stage": stage,
                "command": command,
                "returncode": 0,
                "stdout": "",
                "stderr": "",
            }

        cmd = command.strip()
        if not cmd:
            raise RuntimeCommandError(
                stage=stage,
                command=command,
                stdout="",
                stderr="missing command",
                returncode=127,
            )

        parts = shlex.split(cmd)
        try:
            proc = await asyncio.create_subprocess_exec(
                *parts,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.config.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeCommandError(
                stage=stage,
                command=cmd,
                stdout="",
                stderr=f"timeout after {self.config.timeout_seconds}s",
                returncode=124,
            ) from exc
        except FileNotFoundError as exc:
            raise RuntimeCommandError(
                stage=stage,
                command=cmd,
                stdout="",
                stderr=str(exc),
                returncode=127,
            ) from exc

        stdout = stdout_bytes.decode("utf-8", errors="ignore")
        stderr = stderr_bytes.decode("utf-8", errors="ignore")
        returncode = int(proc.returncode or 0)
        if returncode != 0:
            raise RuntimeCommandError(
                stage=stage,
                command=cmd,
                stdout=stdout,
                stderr=stderr,
                returncode=returncode,
            )

        return {
            "mode": self.config.mode,
            "stage": stage,
            "command": cmd,
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
