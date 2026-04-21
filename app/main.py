from fastapi import FastAPI

app = FastAPI(title="Tameru")


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}
