"""Validated, portable configuration. No network addresses or prices are embedded."""
from __future__ import annotations
from typing import Literal
from datetime import date
from pydantic import BaseModel, ConfigDict, Field, model_validator
from zoneinfo import ZoneInfo

CATEGORIES = ["Electrical", "Heating hot water", "Chilled water", "Domestic hot water", "Sauna", "Cafeteria", "Pool / hot tub", "Cooling towers", "Other"]

class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

class Source(Model):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["bacnet", "modbus", "http_json", "platform_sqlite", "sql_server"]
    enabled: bool = True
    # Addresses, file paths and credentials are entered at runtime only.
    settings: dict = Field(default_factory=dict)

class Term(Model):
    meter_id: str
    factor: float = Field(default=1, ge=-1000000, le=1000000)

class Meter(Model):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    name: str = Field(min_length=1, max_length=120)
    category: str = "Other"
    unit: Literal["kWh", "kWh-thermal", "gal", "m3", "L", "therm", "kBtu", "MMBtu", "kg", "lb", "count"] = "kWh"
    mode: Literal["counter", "rate", "interval", "virtual"] = "counter"
    source_id: str | None = None
    point: dict = Field(default_factory=dict)
    scale: float = 1
    offset: float = 0
    rate_period_seconds: float = Field(default=3600, ge=1, le=86400)
    interval_seconds: int = Field(default=900, ge=1, le=86400)
    max_gap_seconds: int = Field(default=900, ge=1, le=86400)
    rollover: float | None = Field(default=None, gt=0)
    terms: list[Term] = Field(default_factory=list, max_length=100)
    enabled: bool = True
    billable: bool = False
    electricity_charges: Literal["energy_only", "account"] = "energy_only"
    markup_percent: float | None = Field(default=None, ge=0, le=1000)
    display_on_dashboard: Literal["none", "live", "month_to_date"] = "none"
    x: float = Field(default=60, ge=0, le=5000)
    y: float = Field(default=60, ge=0, le=5000)
    @model_validator(mode="after")
    def virtual(self):
        if self.mode == "virtual" and (not self.terms or self.source_id):
            raise ValueError("A virtual meter needs terms and cannot have a network source")
        if self.mode != "virtual" and self.terms:
            raise ValueError("Only virtual meters can contain formula terms")
        return self

class RateSelection(Model):
    schedule: str = Field(default="", max_length=30)
    option: Literal["base", "R", "S"] = "base"
    voltage: Literal["secondary", "primary", "transmission"] = "secondary"
    service: Literal["bundled", "delivery"] = "bundled"
    customer_class: Literal["mandatory", "voluntary"] = "mandatory"
    phase: Literal["single", "poly"] = "poly"

class MonthlyRate(Model):
    month: str
    selection: RateSelection
    @model_validator(mode="after")
    def valid_month(self):
        from .periods import month_date
        from .rates import normalize_schedule
        month_date(self.month)
        self.selection.schedule=normalize_schedule(self.selection.schedule)
        return self

class Configuration(Model):
    timezone: str = "America/Los_Angeles"
    markup_percent: float = Field(default=0, ge=0, le=1000)
    poll_seconds: int = Field(default=60, ge=30, le=3600)
    sources: list[Source] = Field(default_factory=list, max_length=100)
    meters: list[Meter] = Field(default_factory=list, max_length=500)
    rate: RateSelection = Field(default_factory=RateSelection)
    monthly_rates: list[MonthlyRate] = Field(default_factory=list, max_length=1200)
    @model_validator(mode="after")
    def validate_graph(self):
        ZoneInfo(self.timezone)
        if len({r.month for r in self.monthly_rates})!=len(self.monthly_rates):
            raise ValueError("Each billing month must have only one rate schedule assignment")
        meters = {m.id: m for m in self.meters}
        sources = {s.id for s in self.sources}
        if len(meters) != len(self.meters) or len(sources) != len(self.sources):
            raise ValueError("Meter and source IDs must be unique")
        def visit(mid, path):
            if mid in path: raise ValueError("Meter formula contains a cycle")
            m = meters[mid]
            for term in m.terms:
                if term.meter_id not in meters: raise ValueError("Formula references an unknown meter")
                if meters[term.meter_id].unit != m.unit: raise ValueError("Formula inputs must have matching units; configure scaling first")
                visit(term.meter_id, path | {mid})
        for m in self.meters:
            if m.source_id and m.source_id not in sources: raise ValueError("Unknown meter source")
            visit(m.id, set())
        return self

class Reading(Model):
    meter_id: str
    timestamp: float = Field(gt=0)
    value: float
    stream_id: str | None = Field(default=None, max_length=64)
    quality: Literal["good", "unverified", "fault", "offline"] = "good"
    error: str | None = Field(default=None, max_length=500)

class Allocation(Model):
    meter_id: str
    weight: float = Field(default=1, gt=0, le=1000000)

class Expense(Model):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    description: str = Field(min_length=1, max_length=200)
    category: str = "Other"
    billed_on: date
    period_start: date
    period_end: date
    amount: float = Field(gt=0, le=1000000000, multiple_of=0.01)
    allocations: list[Allocation] = Field(min_length=1, max_length=500)
    @model_validator(mode="after")
    def dates(self):
        if self.period_end <= self.period_start: raise ValueError("Period end must be after start (end is exclusive)")
        if len({a.meter_id for a in self.allocations}) != len(self.allocations): raise ValueError("Duplicate allocation meter")
        return self
