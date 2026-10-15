from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.errors import DomainError
from app.modules.didox.api import router as didox_router
from app.modules.periods.api import router as periods_router
from app.modules.shipments.api import router as shipments_router
from app.modules.warehouses.api import router as warehouses_router

app = FastAPI(title="GSM — учёт топлива", version="0.1.0")


@app.exception_handler(DomainError)
async def domain_error(_: Request, exc: DomainError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "details": exc.details},
    )


@app.get("/health")
def health():
    return {"status": "ok"}


for r in (shipments_router, didox_router, warehouses_router, periods_router):
    app.include_router(r)
