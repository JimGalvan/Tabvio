class SessionNotWaitingForInputError(RuntimeError):
    pass


class SessionNotReadyForFollowUpError(RuntimeError):
    pass


class SensitiveInputNotPendingError(RuntimeError):
    pass
