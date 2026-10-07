"""Configuration from environment variables (prefix ``PCBIS_``) or a ``.env`` file."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from pcb_inspection.domain.gates import QualityThresholds
from pcb_inspection.engine.base import InspectParams, MaskStrategy


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PCBIS_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://pcbis:pcbis@localhost:5432/pcbis"
    redis_url: str = "redis://localhost:6379/0"

    storage_backend: Literal["local", "s3"] = "local"
    storage_path: str = "./data"
    s3_endpoint: str | None = None
    s3_bucket: str = "pcbis"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"

    max_upload_mb: int = 25
    task_time_limit_s: int = 120
    task_max_retries: int = 2
    max_poll_wait_s: int = 30
    reference_cache_size: int = 8
    rate_limit_per_min: int = 0  # 0 disables rate limiting

    # web console at /ui; disabled unless a password is set
    ui_password: SecretStr | None = None
    ui_session_hours: int = 12

    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    engine: str = "classic-diff"
    engine_work_width: int = 3000
    engine_threshold: float = 20.0
    engine_extent_threshold: float = 12.0
    engine_min_area: int = 40
    engine_tol_px: int = 2
    engine_highlight_clip: int = Field(default=100, ge=0, le=255)
    engine_open_radius: int = Field(default=4, ge=0)
    engine_background_sigma: float = Field(default=20.0, ge=0)
    default_mask_strategy: MaskStrategy = MaskStrategy.FULL_FRAME
    # targeted detectors next to the difference map (see docs/algorithm.md); off by default
    detect_solder: bool = False
    detect_solder_delta: float = Field(default=25.0, ge=0)
    detect_shift: bool = False
    detect_shift_px: float = Field(default=4.0, gt=0)
    detect_specks: bool = False
    detect_speck_threshold: float = Field(default=35.0, gt=0)
    detect_speck_min_area: int = Field(default=60, ge=1)
    detect_hairs: bool = False
    # passed boards of the same reference used as extra references by the detectors
    reference_pool_size: int = Field(default=2, ge=0, le=5)

    gate_min_width: int = 1500
    gate_min_inliers: int = 50
    gate_min_sharpness_ratio: float = 0.90
    gate_max_lab_shift_l: float = 8.0
    gate_max_lab_shift_ab: float = 5.0
    gate_max_differences: int = 60
    gate_max_differences_area_ratio: float = Field(default=0.02, ge=0, le=1)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def inspect_params(self) -> InspectParams:
        return InspectParams(
            work_width=self.engine_work_width,
            threshold=self.engine_threshold,
            extent_threshold=self.engine_extent_threshold,
            min_area=self.engine_min_area,
            tol_px=self.engine_tol_px,
            highlight_clip=self.engine_highlight_clip,
            open_radius=self.engine_open_radius,
            background_sigma=self.engine_background_sigma,
            detect_solder=self.detect_solder,
            solder_delta=self.detect_solder_delta,
            detect_shift=self.detect_shift,
            shift_px=self.detect_shift_px,
            detect_specks=self.detect_specks,
            speck_threshold=self.detect_speck_threshold,
            speck_min_area=self.detect_speck_min_area,
            detect_hairs=self.detect_hairs,
        )

    def thresholds(self) -> QualityThresholds:
        return QualityThresholds(
            min_width=self.gate_min_width,
            min_inliers=self.gate_min_inliers,
            min_sharpness_ratio=self.gate_min_sharpness_ratio,
            max_lab_shift_l=self.gate_max_lab_shift_l,
            max_lab_shift_ab=self.gate_max_lab_shift_ab,
            max_differences=self.gate_max_differences,
            max_differences_area_ratio=self.gate_max_differences_area_ratio,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
