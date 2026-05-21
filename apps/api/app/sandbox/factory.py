"""Driver factory — picks docker or local based on settings."""

from __future__ import annotations

from loguru import logger

from app.config import Settings, get_settings
from app.sandbox.driver import SandboxDriver


def build_driver(settings: Settings | None = None) -> SandboxDriver:
    """Return a fresh driver instance per the configured ``SANDBOX_DRIVER``."""
    s = settings or get_settings()
    if s.sandbox_driver == "docker":
        from app.sandbox.docker_driver import DockerSandboxDriver

        return DockerSandboxDriver()

    from app.sandbox.local_driver import LocalSandboxDriver

    if s.arena_env == "production":
        # Tripwire — refuse to silently run insecurely.
        logger.error("SANDBOX_DRIVER=local is forbidden in production — refusing to start")
        raise RuntimeError("local sandbox driver is forbidden in production")
    return LocalSandboxDriver()
