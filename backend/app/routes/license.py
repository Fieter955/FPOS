"""Local API facade for the central lifetime license service."""

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException

from .. import models
from ..auth import get_current_user, require_admin
from ..services import central_license


router = APIRouter()


class ActivationRequest(BaseModel):
    license_key: str = Field(min_length=8, max_length=64)


class SelectionRequest(BaseModel):
    selected_installation_id: str = Field(min_length=16, max_length=64)
    confirmation_hostname: str = Field(min_length=1, max_length=128)


class ConfigurationRequest(BaseModel):
    license_server_url: str = Field(min_length=8, max_length=500)
    public_key_b64: str = Field(min_length=40, max_length=100)
    fpos_url: str = Field(default="", max_length=500)


class AutostartRequest(BaseModel):
    enabled: bool


def run_license_action(action, *args):
    try:
        return action(*args)
    except ConnectionError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/status")
def get_license_status():
    return central_license.public_status()


@router.post("/activate")
def activate_license(payload: ActivationRequest, _user=Depends(require_admin)):
    return run_license_action(central_license.activate, payload.license_key)


@router.post("/check")
def check_license(_user=Depends(require_admin)):
    return run_license_action(central_license.check_now)


@router.post("/select-server")
def select_server(payload: SelectionRequest, _user=Depends(require_admin)):
    return run_license_action(
        central_license.select_server,
        payload.selected_installation_id,
        payload.confirmation_hostname,
    )


@router.post("/reclaim")
def reclaim_server(_user=Depends(require_admin)):
    return run_license_action(central_license.reclaim)


@router.post("/developer/configure")
def configure_license(
    payload: ConfigurationRequest,
    user: models.User = Depends(get_current_user),
):
    if user.username.lower() != "fieter":
        raise HTTPException(403, "Hanya akun Fieter yang dapat mengubah server lisensi")
    return run_license_action(
        central_license.configure,
        payload.license_server_url,
        payload.public_key_b64,
        payload.fpos_url,
    )


@router.post("/autostart")
def set_autostart(payload: AutostartRequest, _user=Depends(require_admin)):
    run_license_action(central_license.set_windows_autostart, payload.enabled)
    return {"success": True, "enabled": payload.enabled}
