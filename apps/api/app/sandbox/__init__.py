"""Sandbox layer — per-session ephemeral environments.

Two drivers ship: :class:`DockerSandboxDriver` for prod and
:class:`LocalSandboxDriver` for laptops without Docker. The :class:`SandboxPool`
gates concurrency and runs an idle reaper.
"""

from app.sandbox.driver import SandboxDriver
from app.sandbox.factory import build_driver
from app.sandbox.pool import SandboxPool
from app.sandbox.types import (
    ApplyResult,
    FileTreeNode,
    GradingArtifacts,
    RunResult,
    SandboxHandle,
)

__all__ = [
    "ApplyResult",
    "FileTreeNode",
    "GradingArtifacts",
    "RunResult",
    "SandboxDriver",
    "SandboxHandle",
    "SandboxPool",
    "build_driver",
]
