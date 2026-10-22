"""Application exception hierarchy and safe error boundary.

Errors carry enough detail for logs/debugging but render user-safe messages,
never raw provider payloads or internal stack traces.
"""

from __future__ import annotations


class AppError(Exception):
    """Base application error with a safe user-facing message."""

    #: user-facing message; subclasses override. Never contains secrets/raw payloads.
    safe_message: str = "An internal error occurred."

    def __init__(self, detail: str = "", *, safe_message: str | None = None) -> None:
        self.detail = detail or self.__class__.__name__
        if safe_message:
            self.safe_message = safe_message
        super().__init__(self.detail)


class ConfigError(AppError):
    """Invalid or missing configuration (boot-time, fail-fast)."""
    safe_message = "Service configuration is invalid."


class RegistryError(ConfigError):
    """Agent registry validation failure (unknown server, duplicate, bad YAML)."""
    safe_message = "Agent registry configuration is invalid."


class PlanValidationError(AppError):
    """A produced Plan violated its contract (unknown agent/tool/cycle)."""
    safe_message = "The request could not be planned reliably."


class OrchestrationError(AppError):
    """Graph execution failure (node crash, budget exhaustion, deadlock)."""
    safe_message = "The request could not be completed."



class MCPError(AppError):
    """Base MCP integration failure."""
    safe_message = "A tool service is currently unavailable."


class MCPConnectionError(MCPError):
    safe_message = "A tool service could not be reached."


class MCPToolPermissionError(MCPError):
    """Agent attempted a tool outside its allowed set / permission tier."""
    safe_message = "The requested tool is not permitted for this agent."


class MCPTimeoutError(MCPError):
    safe_message = "A tool call timed out."


class MCPCircuitOpenError(MCPError):
    """Circuit breaker tripped for a server (fail fast, no retries)."""
    safe_message = "A tool service is temporarily unavailable."



class AIError(AppError):
    """Base AI provider failure."""
    safe_message = "The AI service is currently unavailable."


class AIStructuredOutputError(AIError):
    """Model output failed schema validation after repair retries."""
    safe_message = "The AI service returned an unusable response."


class AITimeoutError(AIError):
    safe_message = "The AI service timed out."
