from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class Category(str, Enum):
    self_study = "Self-study"
    meeting = "Meeting"
    other = "Other"


class EntryStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class EntryCreate(BaseModel):
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    activity: str = Field(..., min_length=1, max_length=255)
    category: Category
    hours: float = Field(..., gt=0, le=24)
    force: bool = False


class EntryUpdate(BaseModel):
    date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    activity: Optional[str] = Field(None, min_length=1, max_length=255)
    category: Optional[Category] = None
    hours: Optional[float] = Field(None, gt=0, le=24)


class EntryResponse(BaseModel):
    id: int
    user_id: int
    username: Optional[str] = None
    date: str
    activity: str
    category: str
    hours: float
    status: str
    overtime: bool = False
    created_at: str


class StatsResponse(BaseModel):
    hours_today: float
    hours_week: float
    total_entries: int


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    username: str
    user_id: int


class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    hourly_rate: float


class UpdateRateRequest(BaseModel):
    hourly_rate: float = Field(..., ge=0)


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)
    role: str = Field(..., pattern="^(intern|manager)$")
    hourly_rate: float = Field(0.0, ge=0)


class WeeklyReportDay(BaseModel):
    date: str
    self_study: float
    meeting: float
    other: float
    total: float
    approved_pay: float = 0.0
    any_overtime: bool = False
    user_breakdown: list = []


class WeeklyReport(BaseModel):
    from_date: str
    to_date: str
    days: list[WeeklyReportDay]
    category_totals: dict
    total_hours: float
    hourly_rate: float
    total_pay: float
    entries: list[EntryResponse]
