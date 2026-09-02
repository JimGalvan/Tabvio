class CredentialError(Exception):
    """Base class for safe credential errors."""


class CredentialNotFoundError(CredentialError):
    pass


class CredentialConflictError(CredentialError):
    pass


class CredentialConfigurationError(CredentialError):
    pass


class CredentialInvalidError(CredentialError):
    pass


class CredentialDomainDeniedError(CredentialError):
    pass
