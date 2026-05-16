"""
Pydantic models for API request/response serialization.
"""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ──────────────────────────
# Account Models
# ──────────────────────────

class AccountCreate(BaseModel):
    phone: str
    api_id: int
    api_hash: str
    display_name: Optional[str] = None


class AccountLoginCode(BaseModel):
    phone: str
    phone_code_hash: str
    code: str
    password: Optional[str] = None  # for 2FA


class AccountOut(BaseModel):
    id: str
    phone: str
    display_name: Optional[str]
    status: str
    floodwait_until: Optional[datetime]
    total_uploaded: int
    created_at: datetime

    class Config:
        from_attributes = True


# ──────────────────────────
# Session / Upload Models
# ──────────────────────────

class StartUploadRequest(BaseModel):
    root_folder: str = Field(..., description="Absolute path to root photo folder")
    channel_id: str = Field(..., description="Telegram channel ID or username")
    account_id: str = Field(..., description="Account UUID to use")
    session_name: Optional[str] = Field(None, description="Label for this upload session")


class SessionOut(BaseModel):
    id: str
    name: str
    root_folder: str
    channel_id: str
    account_id: Optional[str]
    status: str
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    total_files: int
    uploaded_files: int
    failed_files: int
    created_at: datetime

    class Config:
        from_attributes = True


# ──────────────────────────
# Progress Models
# ──────────────────────────

class ProgressEvent(BaseModel):
    """Sent via SSE to frontend."""
    event_type: str   # progress | floodwait | log | complete | error
    session_id: str
    current_folder: Optional[str] = None
    current_file: Optional[str] = None
    current_album: Optional[int] = None
    uploaded_files: int = 0
    total_files: int = 0
    failed_files: int = 0
    speed_bps: Optional[float] = None
    eta_seconds: Optional[float] = None
    floodwait_seconds: Optional[int] = None
    floodwait_account: Optional[str] = None
    message: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class FloodwaitEvent(BaseModel):
    account_id: str
    account_phone: str
    wait_seconds: int
    resume_at: datetime


# ──────────────────────────
# Folder Preview
# ──────────────────────────

class FolderScanResult(BaseModel):
    root: str
    folders: List["FolderInfo"]
    total_files: int
    total_size_bytes: int
    total_size_human: str


class FolderInfo(BaseModel):
    path: str
    name: str
    photo_count: int
    size_bytes: int
    size_human: str


FolderScanResult.model_rebuild()


# ──────────────────────────
# Log Models
# ──────────────────────────

class LogOut(BaseModel):
    id: str
    session_id: Optional[str]
    level: str
    message: str
    context: Optional[dict]
    created_at: datetime

    class Config:
        from_attributes = True


# ──────────────────────────
# Failed Upload Models
# ──────────────────────────

class FailedFileOut(BaseModel):
    id: str
    file_id: str
    session_id: str
    filename: str
    path: str
    error_type: str
    error_message: str
    occurred_at: datetime
    resolved: bool

    class Config:
        from_attributes = True


class RetryFailedRequest(BaseModel):
    file_ids: List[str]


# ──────────────────────────
# App settings (YAML-backed)
# ──────────────────────────


class SettingsOut(BaseModel):
    app_name: str
    folder_start_template: str
    folder_end_template: str


class SettingsPatch(BaseModel):
    app_name: Optional[str] = None
    folder_start_template: Optional[str] = None
    folder_end_template: Optional[str] = None
