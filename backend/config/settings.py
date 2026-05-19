"""
Application settings loaded from config.yaml and environment variables.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import List

import yaml
from pydantic import field_validator
from pydantic_settings import BaseSettings


_CONFIG_PATH = Path(__file__).parent.parent.parent / "config.yaml"


def _load_yaml() -> dict:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


class Settings(BaseSettings):
    # App
    app_name: str = "TeleGallery"
    app_version: str = "1.0.0"
    data_dir: str = "./data"
    log_level: str = "INFO"

    # Backend server
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    backend_reload: bool = False

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/telegallery.db"

    # Upload engine
    album_size: int = 5
    max_retries: int = 5
    retry_base_delay: int = 5
    retry_max_delay: int = 300
    upload_album_timeout: int = 900
    upload_photo_timeout: int = 600
    jpeg_quality: int = 85
    concurrent_uploads: int = 1
    failover_on_floodwait: bool = True
    photo_extensions: List[str] = [".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"]

    # Templates
    folder_start_template: str = "🎉 {folder_name} PHOTOS START 🎉"
    folder_end_template: str = "✅ {folder_name} PHOTOS END ✅"

    # Telegram
    floodwait_safety_buffer: int = 5
    session_dir: str = "./data/sessions"

    # Monitoring & Self-Healing (v2)
    stall_detection_threshold: int = 300       # seconds before declaring stall
    health_check_interval: int = 10            # seconds between health samples
    metrics_persist_interval: int = 30         # seconds between DB snapshots
    dashboard_broadcast_interval: int = 5      # seconds between SSE dashboard updates
    ema_alpha: float = 0.3                     # EMA smoothing factor (0.1=slow, 0.5=fast)
    rolling_window_size: int = 200             # recent uploads to keep for statistics
    max_concurrent_uploads: int = 4            # hard ceiling for adaptive controller
    worker_recycle_after: int = 500            # restart worker after N uploads
    recovery_cooldown: int = 60                # seconds between same recovery action

    class Config:
        env_file = ".env"
        extra = "ignore"

    @classmethod
    def from_yaml(cls) -> "Settings":
        cfg = _load_yaml()
        flat = {}

        if "app" in cfg:
            flat["app_name"] = cfg["app"].get("name", "TeleGallery")
            flat["app_version"] = cfg["app"].get("version", "1.0.0")
            flat["data_dir"] = cfg["app"].get("data_dir", "./data")
            flat["log_level"] = cfg["app"].get("log_level", "INFO")

        if "backend" in cfg:
            flat["backend_host"] = cfg["backend"].get("host", "127.0.0.1")
            flat["backend_port"] = cfg["backend"].get("port", 8000)
            flat["backend_reload"] = cfg["backend"].get("reload", False)

        if "database" in cfg:
            flat["database_url"] = cfg["database"].get(
                "url", "sqlite+aiosqlite:///./data/telegallery.db"
            )

        if "upload" in cfg:
            flat["album_size"] = cfg["upload"].get("album_size", 5)
            flat["max_retries"] = cfg["upload"].get("max_retries", 5)
            flat["retry_base_delay"] = cfg["upload"].get("retry_base_delay", 5)
            flat["retry_max_delay"] = cfg["upload"].get("retry_max_delay", 300)
            flat["upload_album_timeout"] = cfg["upload"].get("upload_album_timeout", 900)
            flat["upload_photo_timeout"] = cfg["upload"].get("upload_photo_timeout", 600)
            flat["jpeg_quality"] = cfg["upload"].get("jpeg_quality", 85)
            flat["concurrent_uploads"] = cfg["upload"].get("concurrent_uploads", 1)
            flat["failover_on_floodwait"] = cfg["upload"].get("failover_on_floodwait", True)
            flat["photo_extensions"] = cfg["upload"].get(
                "photo_extensions", [".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"]
            )

        if "templates" in cfg:
            flat["folder_start_template"] = cfg["templates"].get(
                "folder_start", "🎉 {folder_name} PHOTOS START 🎉"
            )
            flat["folder_end_template"] = cfg["templates"].get(
                "folder_end", "✅ {folder_name} PHOTOS END ✅"
            )

        if "telegram" in cfg:
            flat["floodwait_safety_buffer"] = cfg["telegram"].get(
                "floodwait_safety_buffer", 5
            )
            flat["session_dir"] = cfg["telegram"].get("session_dir", "./data/sessions")

        if "monitoring" in cfg:
            flat["stall_detection_threshold"] = cfg["monitoring"].get("stall_detection_threshold", 300)
            flat["health_check_interval"] = cfg["monitoring"].get("health_check_interval", 10)
            flat["metrics_persist_interval"] = cfg["monitoring"].get("metrics_persist_interval", 30)
            flat["dashboard_broadcast_interval"] = cfg["monitoring"].get("dashboard_broadcast_interval", 5)
            flat["ema_alpha"] = cfg["monitoring"].get("ema_alpha", 0.3)
            flat["rolling_window_size"] = cfg["monitoring"].get("rolling_window_size", 200)
            flat["max_concurrent_uploads"] = cfg["monitoring"].get("max_concurrent_uploads", 4)
            flat["worker_recycle_after"] = cfg["monitoring"].get("worker_recycle_after", 500)
            flat["recovery_cooldown"] = cfg["monitoring"].get("recovery_cooldown", 60)

        return cls(**flat)


@lru_cache()
def get_settings() -> Settings:
    return Settings.from_yaml()


def save_yaml_updates(
    *,
    folder_start_template: str | None = None,
    folder_end_template: str | None = None,
    app_name: str | None = None,
) -> None:
    """Merge updates into config.yaml and reload cached settings."""
    cfg = _load_yaml()
    if folder_start_template is not None or folder_end_template is not None:
        t = cfg.setdefault("templates", {})
        if folder_start_template is not None:
            t["folder_start"] = folder_start_template
        if folder_end_template is not None:
            t["folder_end"] = folder_end_template
    if app_name is not None:
        cfg.setdefault("app", {})["name"] = app_name
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            cfg,
            f,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
    get_settings.cache_clear()
