"""凭据契约（v0.7 Task 14.5，dsh credentials 移植）。

- 配置面**只存环境变量名引用**，绝不落盘明文。
- 每操作解析一次；空值（或未设置）视为**不存在**（fail loud）。
- 分层解析：裸引用 = file 优先、env 兜底；显式前缀 ``env:`` / ``file:`` 强制来源。

契约出处见 ``docs/porting/2026-08-14-dsh-porting.md``（MIT，dsh ``47f943859b``）。
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["CredentialMissingError", "CredentialResolver"]


class CredentialMissingError(RuntimeError):
    """引用的凭据不存在（未设置或为空）。"""


class CredentialResolver:
    """按引用解析凭据；只读环境变量与可选 .env 文件。"""

    def __init__(self, file_path: str | Path | None = None, env: dict[str, str] | None = None) -> None:
        self._env = env if env is not None else os.environ
        self._file_values: dict[str, str] = {}
        if file_path is not None:
            path = Path(file_path)
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    self._file_values[key.strip()] = value.strip()

    def resolve(self, reference: str) -> str:
        """解析引用；未设置或空值抛 ``CredentialMissingError``。"""
        if reference.startswith("env:"):
            value = self._env.get(reference[4:], "")
        elif reference.startswith("file:"):
            value = self._file_values.get(reference[5:], "")
        else:
            value = self._file_values.get(reference) or self._env.get(reference, "")
        if not value:
            raise CredentialMissingError(f"credential {reference!r} is unset or empty")
        return value
