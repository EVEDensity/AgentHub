from __future__ import annotations

import uvicorn

from .config import RunnerServiceSettings


def main() -> None:
    settings = RunnerServiceSettings()  # type: ignore[call-arg]
    uvicorn.run(
        "services.python.runner_service.main:app",
        host=settings.host,
        port=settings.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
