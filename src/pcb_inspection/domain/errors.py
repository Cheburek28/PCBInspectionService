"""Application errors. The API layer turns them into RFC 9457 problem+json responses."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status: int = 400
    code: str = "BAD_REQUEST"
    title: str = "Bad request"

    def __init__(self, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.detail = detail
        self.extra = extra


class NotFound(AppError):
    status, code, title = 404, "NOT_FOUND", "Resource not found"


class Unauthorized(AppError):
    status, code, title = 401, "UNAUTHORIZED", "Missing or invalid API key"


class SessionClosed(AppError):
    status, code, title = 409, "SESSION_CLOSED", "Session is closed"


class NoActiveReference(AppError):
    status, code, title = 409, "NO_ACTIVE_REFERENCE", "No active reference for this side"


class NoTransform(AppError):
    status, code, title = 409, "NO_TRANSFORM", "Inspection has no alignment transform"


class InvalidState(AppError):
    status, code, title = 409, "INVALID_STATE", "Operation not allowed in the current state"


class IdempotencyConflict(AppError):
    status, code, title = 409, "IDEMPOTENCY_CONFLICT", "Idempotency key was used for a different request"


class PayloadTooLarge(AppError):
    status, code, title = 413, "PAYLOAD_TOO_LARGE", "Upload is too large"


class UnsupportedMediaType(AppError):
    status, code, title = 415, "UNSUPPORTED_MEDIA_TYPE", "Unsupported image format"


class ImageUnreadable(AppError):
    status, code, title = 422, "IMAGE_UNREADABLE", "Image cannot be decoded"


class ImageTooSmall(AppError):
    status, code, title = 422, "IMAGE_TOO_SMALL", "Image is too small"


class BoardMaskNotFound(AppError):
    status, code, title = 422, "BOARD_MASK_NOT_FOUND", "Board area not found on the reference image"


class ValidationFailed(AppError):
    status, code, title = 422, "VALIDATION_ERROR", "Request validation failed"


class RateLimited(AppError):
    status, code, title = 429, "RATE_LIMITED", "Too many requests"
