"""Non-authoritative runtime security boundaries."""

from .credential_provider_v1 import (
    CredentialProvider,
    CredentialProviderError,
    CredentialProviderErrorCode,
    CredentialProviderV1,
    resolve_provider_client,
)

__all__ = (
    "CredentialProvider",
    "CredentialProviderError",
    "CredentialProviderErrorCode",
    "CredentialProviderV1",
    "resolve_provider_client",
)
