"""Stable, non-sensitive application errors."""


class DomainError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        *,
        retry_after: int | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = retry_after
        super().__init__(code)
