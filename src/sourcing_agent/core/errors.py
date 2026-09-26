"""Errors shared by every command."""


# Raised for an unreadable or invalid input file, or a refused action.
class InputError(Exception):
    pass


# Raised when a worker cannot produce a result.
class WorkerError(Exception):
    pass
