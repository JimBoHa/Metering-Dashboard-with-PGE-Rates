"""Portable SQL Server connection and trend mapping configuration."""
from typing import Literal
from pydantic import Field, model_validator
from zoneinfo import ZoneInfo
from .models import Model

class TrendMapping(Model):
    schema_name: str = Field(default="dbo", min_length=1, max_length=128)
    table: str = Field(min_length=1, max_length=128)
    point_id_column: str = Field(min_length=1, max_length=128)
    timestamp_column: str = Field(min_length=1, max_length=128)
    value_column: str = Field(min_length=1, max_length=128)
    timestamp_kind: Literal["datetime", "unix_seconds", "unix_milliseconds"] = "datetime"
    quality_column: str | None = Field(default=None, max_length=128)
    point_schema: str = Field(default="dbo", min_length=1, max_length=128)
    point_table: str | None = Field(default=None, max_length=128)
    point_key_column: str | None = Field(default=None, max_length=128)
    point_name_column: str | None = Field(default=None, max_length=128)
    point_path_column: str | None = Field(default=None, max_length=128)
    point_unit_column: str | None = Field(default=None, max_length=128)
    @model_validator(mode="after")
    def point_columns(self):
        if self.point_table and not all((self.point_key_column,self.point_name_column)):
            raise ValueError("A point table needs its key and name columns")
        return self

class SqlSettings(Model):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=1433, ge=1, le=65535)
    database: str = Field(default="", max_length=128)
    username: str = Field(min_length=1, max_length=256)
    tls: Literal["verify", "plain"] = "verify"
    ca_pem: str = Field(default="", max_length=100000)
    profile: Literal["auto", "metasys", "custom"] = "auto"
    mapping: TrendMapping | None = None
    timezone: str = "UTC"
    missing_quality: Literal["unverified", "good"] = "unverified"
    good_quality_values: list[str] = Field(default_factory=lambda:["good", "0"], min_length=1, max_length=30)
    initial_days: int = Field(default=7, ge=1, le=3660)
    overlap_hours: int = Field(default=24, ge=1, le=720)
    sync_seconds: int = Field(default=300, ge=60, le=86400)
    batch_size: int = Field(default=500, ge=50, le=1000)
    @model_validator(mode="after")
    def valid(self):
        ZoneInfo(self.timezone)
        if self.ca_pem:
            from cryptography.x509 import load_pem_x509_certificates
            try:
                if not load_pem_x509_certificates(self.ca_pem.encode()):raise ValueError()
            except ValueError:raise ValueError("The CA certificate must be a valid PEM certificate") from None
        return self
