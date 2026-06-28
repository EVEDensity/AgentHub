from fastapi import FastAPI

app = FastAPI(title="evaluation-batch-service")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "evaluation-batch-service"}


@app.get("/profile")
async def profile() -> dict:
    return {
        "service": "evaluation-batch-service",
        "responsibilities": [
            "regression evaluation",
            "quality scoring",
            "load test replay analysis",
        ],
    }
