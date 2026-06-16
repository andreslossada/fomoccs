from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

__all__ = ["UserCreate", "UserLogin", "UserResponse"]


class UserCreate(BaseModel):
    email: EmailStr
    password: Annotated[str, Field(min_length=8)]
    display_name: Annotated[str | None, Field(max_length=100)] = None

    @field_validator("password")
    @classmethod
    def _validate_password_strength(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("password must contain at least one digit")
        return v


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    display_name: str | None = None
    is_admin: bool = False
    created_at: datetime
