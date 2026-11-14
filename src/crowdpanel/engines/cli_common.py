from __future__ import annotations

import asyncio
import os
import shutil
import tempfile

from .base import EngineError


class CliRunner:
    """Runs a CLI per call in an empty scratch directory, so no project files, memory or
    instructions from the current folder leak into the personas."""

    def __init__(self, binary: str, timeout: float = 240.0, env_drop: tuple[str, ...] = ()):
        path = shutil.which(binary)
        if not path:
            raise EngineError(f"'{binary}' was not found on PATH", retryable=False)
        self.binary = path
        self.timeout = timeout
        self.workdir = tempfile.mkdtemp(prefix="crowdpanel-")
        self.env = {k: v for k, v in os.environ.items() if k not in env_drop}

    async def run(self, args: list[str], stdin: str) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            self.binary, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.workdir,
            env=self.env,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin.encode()), self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise EngineError(f"{os.path.basename(self.binary)} took longer than {self.timeout:.0f}s")
        return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")

    def cleanup(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)
