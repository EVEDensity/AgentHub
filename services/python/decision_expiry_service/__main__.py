from __future__ import annotations

import uvicorn

from .config import DecisionExpiryServiceSettings


def main() -> None:
    settings = DecisionExpiryServiceSettings()
    uvicorn.run(
        "services.python.decision_expiry_service.main:app",
        host=settings.host,
        port=settings.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
