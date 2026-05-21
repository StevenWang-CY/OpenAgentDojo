"""Agent service — deterministic hybrid simulation (M4 work lives here).

Only the module skeleton ships with M0-M2; the implementation is wired in M4.
"""

from app.agent.llm import AnthropicClient
from app.agent.service import AgentService

__all__ = ["AgentService", "AnthropicClient"]
