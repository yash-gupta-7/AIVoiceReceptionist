"""API request/response schemas (Pydantic v2)."""
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class LoginIn(BaseModel):
    email: str = Field(max_length=120)
    password: str = Field(max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class PatientIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=10, max_length=20)
    dob: date
    insurance: str | None = None


class PatientOut(PatientIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class DoctorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    department: str
    branch_id: int


class AppointmentIn(BaseModel):
    patient_id: int
    doctor_id: int
    starts_at: datetime


class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    patient_id: int
    doctor_id: int
    branch_id: int
    starts_at: datetime
    status: str


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    role: str
    text: str
    intent: str | None
    created_at: datetime


class CallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    sid: str
    caller: str
    language: str
    state: str
    outcome: str | None
    started_at: datetime
    ended_at: datetime | None
