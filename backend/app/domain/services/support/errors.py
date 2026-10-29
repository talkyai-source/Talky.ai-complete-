"""Support domain errors. The API layer maps each to the standard envelope."""
from __future__ import annotations


class SupportError(Exception):
    status = 400
    code = "support_error"
    message = "The support request could not be completed."

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message
        if code:
            self.code = code


class SupportNotFound(SupportError):
    status = 404
    code = "not_found"
    message = "Not found."


class SupportForbidden(SupportError):
    """The record exists but belongs to another tenant or person."""
    status = 403
    code = "forbidden"
    message = "You do not have access to this record."


class SupportConflict(SupportError):
    status = 409
    code = "conflict"
    message = "This record changed or the action is not allowed in its current state."


class InvalidTransition(SupportConflict):
    code = "invalid_status_transition"
    message = "That action is not allowed for this ticket's current status."


class VersionConflict(SupportConflict):
    code = "version_conflict"
    message = "This ticket changed after you loaded it. Reload it before saving."


class RequestIdConflict(SupportConflict):
    code = "request_id_conflict"
    message = "This request_id was already used for a different action. Generate a new one."


class RequestInProgress(SupportConflict):
    code = "request_in_progress"
    message = "This request is still being processed. Retry the same request shortly."


class SupportValidationError(SupportError):
    status = 422
    code = "validation_error"
    message = "The request is not valid."


class SupportUnavailable(SupportError):
    status = 503
    code = "support_unavailable"
    message = "Support is temporarily unavailable. Retry the same request."
