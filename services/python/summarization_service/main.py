from fastapi import FastAPI

app = FastAPI(title="summarization-service")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "summarization-service"}


@app.get("/profile")
async def profile() -> dict:
    return {
        "service": "summarization-service",
        "responsibilities": [
            "offline consolidation",
            "session summarization",
            "report generation",
            "memory checkpoint writing",
        ],
    }
