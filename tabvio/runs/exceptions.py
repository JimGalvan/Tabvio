"""Exceptions raised by the run service."""


class RunAlreadyActiveError(RuntimeError):
    pass


class RunCapacityReachedError(RuntimeError):
    pass


class RunNotFoundError(RuntimeError):
    pass


class RunNotWaitingForInputError(RuntimeError):
    pass


class RunNotReadyForFollowUpError(RuntimeError):
    pass

