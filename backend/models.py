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
    rejection_reason: Optional[str] = None
    overtime: bool = False
    created_at: str


class RejectRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


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
    email: Optional[str] = None


class UpdateEmailRequest(BaseModel):
    email: Optional[str] = Field(None, max_length=254)


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


class BasicUser(BaseModel):
    id: int
    username: str
    role: str


class AttendeeInfo(BaseModel):
    id: int
    username: str
    status: str = "pending"
    decline_reason: Optional[str] = None


class RSVPRequest(BaseModel):
    status: str = Field(..., pattern="^(accepted|declined)$")
    reason: Optional[str] = Field(None, max_length=500)


class RoomCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    capacity: Optional[int] = Field(None, ge=1)
    equipment: Optional[str] = Field(None, max_length=300)


class RoomUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    capacity: Optional[int] = Field(None, ge=1)
    equipment: Optional[str] = Field(None, max_length=300)


class RoomResponse(BaseModel):
    id: int
    name: str
    capacity: Optional[int] = None
    equipment: Optional[str] = None


class ForgotPasswordRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=6)


class MeetingCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    start_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    location_type: str = Field("online", pattern="^(online|in_person)$")
    room: Optional[str] = Field(None, max_length=100)
    meeting_link: Optional[str] = Field(None, max_length=500)
    use_google_meet: bool = False
    attendee_ids: list[int] = []
    recurrence: str = Field("none", pattern="^(none|daily|weekly|biweekly|monthly)$")
    recurrence_until: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class MeetingReschedule(BaseModel):
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    start_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")


class MeetingResponse(BaseModel):
    id: int
    organizer_id: int
    organizer_username: str
    title: str
    description: Optional[str] = None
    date: str
    start_time: str
    end_time: str
    location_type: str = "online"
    room: Optional[str] = None
    meeting_link: Optional[str] = None
    attendees: list[AttendeeInfo] = []
    created_at: str
    recurrence_rule: str = "none"
    recurrence_group_id: Optional[str] = None
    series_count: Optional[int] = None
    skipped_dates: Optional[list[str]] = None
    ics_sequence: int = 0