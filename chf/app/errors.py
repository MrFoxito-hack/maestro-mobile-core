class ChargingError(Exception):
    """Domain error; no HTTP server dependency. Causes may be profile extensions."""

    def __init__(self, status: int, cause: str, detail: str):
        self.status, self.cause, self.detail = status, cause, detail
        super().__init__(detail)
