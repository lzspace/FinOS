"""Key sources and authenticated encryption; no network calls are made here."""

from __future__ import annotations

import hashlib
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

    def has_key(self) -> bool:
        try:
            self.get_key()
        except RuntimeError:
            return False
        return True

    def get_or_create_key(self) -> bytes:
        try:
            return self.get_key()
        except RuntimeError:
            key = Fernet.generate_key()
            self.replace_key(key)
            return key

    def replace_key(self, key: bytes) -> None:
        # Constructing Fernet validates length and URL-safe base64 encoding.
        Fernet(key)
        try:
            import keyring
        except ImportError as exc:
            raise RuntimeError("OS keychain support is unavailable") from exc
        keyring.set_password(self.service, self.username, key.decode("ascii"))


@dataclass
class MutableStaticKeyProvider:
    """Mutable synthetic provider used by isolated recovery tests."""

    key: bytes

    def get_key(self) -> bytes:
        return self.key

    def replace_key(self, key: bytes) -> None:
        Fernet(key)
        self.key = key


def cipher_for(provider: KeyProvider) -> Fernet:
    return Fernet(provider.get_key())


def key_fingerprint(provider: KeyProvider) -> str:
    """Return a non-secret identifier suitable for local status displays."""
    return hashlib.sha256(provider.get_key()).hexdigest()[:16]
