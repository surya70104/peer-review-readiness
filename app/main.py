from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app import db
from pathlib import Path

from fastapi.staticfiles import StaticFiles

from app.routers import pages, profile, readiness, registers, reviews


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        db.init_pool()
    except Exception as exc:  # server still starts; /health will report the failure
        print(f"[startup] database not reachable: {exc}")
    yield
    db.close_pool()


app = FastAPI(title="OAK Audit · Peer Review Readiness", lifespan=lifespan)
app.include_router(profile.router)
app.include_router(registers.router)
app.include_router(readiness.router)
app.include_router(reviews.router)
app.include_router(pages.router)
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent.parent / "static")), name="static")


@app.get("/health")
def health():
    try:
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT current_database(), version()")
            dbname, version = cur.fetchone()
            cur.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """
            )
            tables = [r[0] for r in cur.fetchall()]
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "database": "unreachable", "detail": str(exc)},
        )
    return {
        "status": "ok",
        "database": dbname,
        "postgres_version": version.split(",")[0],
        "tables": tables,
    }
