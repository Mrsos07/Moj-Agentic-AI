"""
AEOS exception hierarchy.

Keeping a dedicated hierarchy lets every layer raise/catch domain-specific
errors without leaking implementation details across module boundaries.
"""
from __future__ import annotations


class AEOSError(Exception):
    """Base class for every AEOS error."""


class ConfigurationError(AEOSError):
    """Raised when a component is misconfigured or a dependency is missing."""


class EmployeeNotFoundError(AEOSError):
    """Raised when an employee configuration cannot be located."""


class RuntimeLifecycleError(AEOSError):
    """Raised when the runtime lifecycle is violated (e.g. double-load)."""


class CapabilityError(AEOSError):
    """Raised when a capability fails or is unavailable for an employee."""


class CapabilityNotFoundError(CapabilityError):
    """Raised when a requested capability is not registered."""


class MCPError(AEOSError):
    """Raised when an MCP server call fails."""


class MCPNotFoundError(MCPError):
    """Raised when a required MCP server is not registered."""


class MemoryError_(AEOSError):
    """Raised when a memory operation fails."""


class SecurityError(AEOSError):
    """Raised when encryption/decryption or secret access fails."""


class SchedulingError(AEOSError):
    """Raised when a job cannot be scheduled or enqueued."""


class IsolationViolationError(AEOSError):
    """Raised when an employee attempts to access another employee's resources."""
