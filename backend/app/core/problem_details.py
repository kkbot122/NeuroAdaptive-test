"""
RFC 7807 (application/problem+json) error responses for T5 (abuse/DoS/cost
controls): quota exhaustion, rate limiting, and regeneration caps must
return a structured, actionable body -- never a silent failure, a hang, or
an unhandled exception (mandate).

FastAPI's plain HTTPException always serializes as {"detail": ...} with
media type application/json; there is no way to change its content-type
from inside a route. ProblemDetailException + the handler registered in
main.py are what make application/problem+json actually happen.
"""
from typing import Any, Dict, Optional

from fastapi import Request
from fastapi.responses import JSONResponse


class ProblemDetailException(Exception):
    def __init__(
        self,
        status_code: int,
        type_: str,
        title: str,
        detail: str,
        extra: Optional[Dict[str, Any]] = None,
    ):
        self.status_code = status_code
        self.type_ = type_
        self.title = title
        self.detail = detail
        self.extra = extra or {}
        super().__init__(detail)

    def body(self) -> Dict[str, Any]:
        return {
            "type": self.type_,
            "title": self.title,
            "status": self.status_code,
            "detail": self.detail,
            **self.extra,
        }


async def problem_detail_exception_handler(request: Request, exc: ProblemDetailException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.body(), media_type="application/problem+json")
