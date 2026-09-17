from __future__ import annotations

import uvicorn

from .config import VerifierServiceSettings


def main() -> None:
    settings = VerifierServiceSettings()  # type: ignore[call-arg]
    uvicorn.run(
        "services.python.verifier_service.main:app",
        host=settings.host,
        port=settings.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
