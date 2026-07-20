"""Key sources and authenticated encryption; no network calls are made here."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from cryptography.fernet import Fernet


class KeyProvider(Protocol):
    def get_key(self) -> bytes: ...


@dataclass(frozen=True)
class StaticKeyProvider:
    """Test-only provider. Callers must only use it with synthetic data."""

    key: bytes

    def get_key(self) -> bytes:
        return self.key


class KeychainKeyProvider:
    """Loads a pre-provisioned Fernet key from the OS credential store."""

    def __init__(self, service: str = "agent-os.finance", username: str = "database") -> None:
        self.service, self.username = service, username

    def get_key(self) -> bytes:
        try:
            import keyring
        except ImportError as exc:
            raise RuntimeError("OS keychain support is unavailable") from exc
        value = keyring.get_password(self.service, self.username)
        if not value:
            raise RuntimeError("Finance encryption key is not provisioned in the OS keychain")
        return value.encode("ascii")


def cipher_for(provider: KeyProvider) -> Fernet:
    return Fernet(provider.get_key())
