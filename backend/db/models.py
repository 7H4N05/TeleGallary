"""
SQLAlchemy ORM models for TeleGallery.

Tables:
- accounts          : Telegram accounts
- upload_sessions   : Top-level upload jobs
- folders           : Per-folder state
- files             : Per-file state (source of truth for resume)
- logs              : Structured log entries
- failed_uploads    : Failed upload audit trail
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


# ─────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────

import enum


class AccountStatus(str, enum.Enum):
    active = "active"
    cooldown = "cooldown"
    disconnected = "disconnected"


class SessionStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    paused = "paused"
    completed = "completed"
    failed = "failed"
    stopped = "stopped"


class FolderStatus(str, enum.Enum):
    pending = "pending"
    started = "started"
    completed = "completed"
    failed = "failed"


class FileStatus(str, enum.Enum):
    pending = "pending"
    uploading = "uploading"
    uploaded = "uploaded"
    failed = "failed"
    skipped = "skipped"


class LogLevel(str, enum.Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


# ─────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    phone: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    session_string: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    api_id: Mapped[int] = mapped_column(Integer, nullable=False)
    api_hash: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus), default=AccountStatus.disconnected
    )
    floodwait_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    total_uploaded: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    sessions: Mapped[list["UploadSession"]] = relationship(
        "UploadSession", back_populates="account"
    )


class UploadSession(Base):
    __tablename__ = "upload_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    root_folder: Mapped[str] = mapped_column(String, nullable=False)
    channel_id: Mapped[str] = mapped_column(String, nullable=False)
    # Telegram access_hash for the channel — required to address the peer
    # by numeric ID after a reconnect (MemoryStorage loses it on restart).
    channel_access_hash: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    account_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("accounts.id"), nullable=True
    )
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus), default=SessionStatus.pending
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    total_files: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_files: Mapped[int] = mapped_column(Integer, default=0)
    failed_files: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    account: Mapped[Optional["Account"]] = relationship(
        "Account", back_populates="sessions"
    )
    folders: Mapped[list["Folder"]] = relationship(
        "Folder", back_populates="session", cascade="all, delete-orphan"
    )
    files: Mapped[list["File"]] = relationship(
        "File", back_populates="session", cascade="all, delete-orphan"
    )
    logs: Mapped[list["Log"]] = relationship(
        "Log", back_populates="session", cascade="all, delete-orphan"
    )


class Folder(Base):
    __tablename__ = "folders"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        String, ForeignKey("upload_sessions.id"), nullable=False
    )
    path: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[FolderStatus] = mapped_column(
        Enum(FolderStatus), default=FolderStatus.pending
    )
    start_msg_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    end_msg_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_files: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_files: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    session: Mapped["UploadSession"] = relationship(
        "UploadSession", back_populates="folders"
    )
    files: Mapped[list["File"]] = relationship(
        "File", back_populates="folder", cascade="all, delete-orphan"
    )


class File(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    folder_id: Mapped[str] = mapped_column(
        String, ForeignKey("folders.id"), nullable=False
    )
    session_id: Mapped[str] = mapped_column(
        String, ForeignKey("upload_sessions.id"), nullable=False
    )
    path: Mapped[str] = mapped_column(String, nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    album_index: Mapped[int] = mapped_column(Integer, default=0)
    album_position: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[FileStatus] = mapped_column(
        Enum(FileStatus), default=FileStatus.pending
    )
    message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    folder: Mapped["Folder"] = relationship("Folder", back_populates="files")
    session: Mapped["UploadSession"] = relationship(
        "UploadSession", back_populates="files"
    )
    failed_records: Mapped[list["FailedUpload"]] = relationship(
        "FailedUpload", back_populates="file", cascade="all, delete-orphan"
    )


class Log(Base):
    __tablename__ = "logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    session_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("upload_sessions.id"), nullable=True
    )
    level: Mapped[LogLevel] = mapped_column(Enum(LogLevel), default=LogLevel.INFO)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    session: Mapped[Optional["UploadSession"]] = relationship(
        "UploadSession", back_populates="logs"
    )


class FailedUpload(Base):
    __tablename__ = "failed_uploads"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    file_id: Mapped[str] = mapped_column(
        String, ForeignKey("files.id"), nullable=False
    )
    session_id: Mapped[str] = mapped_column(String, nullable=False)
    error_type: Mapped[str] = mapped_column(String, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)

    file: Mapped["File"] = relationship("File", back_populates="failed_records")
