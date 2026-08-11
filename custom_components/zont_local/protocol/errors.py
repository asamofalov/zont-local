"""Exceptions raised by the ZONT protocol package."""


class ZontError(Exception):
    """Base exception for ZONT protocol errors."""


class ZontConnectionError(ZontError):
    """Raised when the controller cannot be reached."""


class ZontAuthenticationError(ZontError):
    """Raised when the controller rejects the credentials."""


class ZontProtocolError(ZontError):
    """Raised when the controller sends an invalid protocol message."""


class ZontRequestTimeoutError(ZontError):
    """Raised when a protocol response is not received in time."""


class ZontCommandTimeoutError(ZontRequestTimeoutError):
    """Raised when a command response is not received in time."""
