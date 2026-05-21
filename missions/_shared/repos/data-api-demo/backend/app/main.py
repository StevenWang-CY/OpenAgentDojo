"""Tiny FastAPI app — exposes a healthcheck and demo routes.

Most missions test the underlying modules directly via pytest; the HTTP
surface is here so the workspace feels like a real service when the user
runs ``uv run uvicorn app.main:app --reload``.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.calc import calculate_price
from app.format import format_report_ts


class PriceRequest(BaseModel):
    qty: int = Field(ge=0)
    unit: str = Field(description="Unit price as a string Decimal (e.g. '10.00')")


class PriceResponse(BaseModel):
    total: str


class FormatRequest(BaseModel):
    ts: int
    tz: str


class FormatResponse(BaseModel):
    formatted: str


def create_app() -> FastAPI:
    app = FastAPI(title="data-api-demo", version="1.0.0")

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/calc/price", response_model=PriceResponse)
    async def calc_price(req: PriceRequest) -> PriceResponse:
        total: Decimal = calculate_price(req.qty, req.unit)
        return PriceResponse(total=str(total))

    @app.post("/format/ts", response_model=FormatResponse)
    async def format_ts(req: FormatRequest) -> FormatResponse:
        try:
            formatted = format_report_ts(req.ts, req.tz)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return FormatResponse(formatted=formatted)

    return app


app = create_app()
