class DomainError(Exception):
    """Нарушение бизнес-правила. code — машиночитаемый код для интерфейса."""

    status_code = 422

    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class NotFound(DomainError):
    status_code = 404


class Forbidden(DomainError):
    status_code = 403


class Conflict(DomainError):
    status_code = 409


class ExternalUnavailable(DomainError):
    status_code = 503
