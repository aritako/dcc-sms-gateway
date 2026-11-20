"""HTTP-facing API errors.

Domain failures stay with the code that raises them: `InsufficientBalance` and
`UnknownDestination` in `app.core`, `InvalidBatch` in the messages handler.
They carry domain payloads and controllers decide how to render them. Only an
error that must escape a FastAPI dependency — which cannot return a response —
needs `ApiError`, so `app.main` can render it in the uniform envelope.
"""


class ApiError(Exception):
  """An error the API renders as `{"error": CODE}`."""

  def __init__(self, status_code: int, code: str) -> None:
    super().__init__(code)
    self.status_code = status_code
    self.code = code
