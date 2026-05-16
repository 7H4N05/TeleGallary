"""
Runtime settings API — reads / updates config.yaml (templates, app display name).
"""

from fastapi import APIRouter, HTTPException

from config.settings import get_settings, save_yaml_updates
from models.schemas import SettingsOut, SettingsPatch

router = APIRouter()


@router.get("/", response_model=SettingsOut)
async def get_app_settings():
    s = get_settings()
    return SettingsOut(
        app_name=s.app_name,
        folder_start_template=s.folder_start_template,
        folder_end_template=s.folder_end_template,
    )


@router.patch("/", response_model=SettingsOut)
async def patch_app_settings(body: SettingsPatch):
    if (
        body.app_name is None
        and body.folder_start_template is None
        and body.folder_end_template is None
    ):
        raise HTTPException(status_code=400, detail="No fields to update")

    for tpl, label in (
        (body.folder_start_template, "folder_start_template"),
        (body.folder_end_template, "folder_end_template"),
    ):
        if tpl is not None and "{folder_name}" not in tpl:
            raise HTTPException(
                status_code=400,
                detail=f"{label} must contain '{{folder_name}}' placeholder",
            )

    save_yaml_updates(
        folder_start_template=body.folder_start_template,
        folder_end_template=body.folder_end_template,
        app_name=body.app_name,
    )
    s = get_settings()
    return SettingsOut(
        app_name=s.app_name,
        folder_start_template=s.folder_start_template,
        folder_end_template=s.folder_end_template,
    )
