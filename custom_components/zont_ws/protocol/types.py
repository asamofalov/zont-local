"""Shared values used by the ZONT protocol package."""

from dataclasses import dataclass

type ZontCommand = str | int | float


@dataclass(frozen=True, slots=True)
class ZontCredentials:
    """Credentials used to authenticate with the controller."""

    username: str
    password: str
