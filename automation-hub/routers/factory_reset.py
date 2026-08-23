"""Protected application Factory Reset endpoints."""
from __future__ import annotations

import webhook_api as _wa
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/system/factory-reset", tags=["system"])


class FactoryResetRequest(BaseModel):
    confirmation: str
    final_confirmation: bool = False


def _service():
    service = getattr(_wa, "factory_reset_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Factory Reset service is unavailable")
    return service


def _initiator(request: Request) -> str:
    principal = getattr(request.state, "supabase_principal", None)
    if principal is not None:
        return str(getattr(principal, "email", None) or getattr(principal, "id", "supabase-admin"))
    return _wa.request_user(request)


@router.get("")
def factory_reset_status():
    return _service().status()


@router.post("")
def factory_reset(body: FactoryResetRequest, request: Request,
                  x_webhook_secret: str = Header(default="")):
    _wa._check_secret(x_webhook_secret)
    service = _service()
    try:
        return service.run(
            initiated_by=_initiator(request), confirmation=body.confirmation,
            final_confirmation=body.final_confirmation,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        status = 409 if "already running" in str(exc) else 503
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Factory Reset failed; trading remains stopped: {type(exc).__name__}: {exc}",
        ) from exc
