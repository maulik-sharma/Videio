import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, StringConstraints

Password = Annotated[str, StringConstraints(min_length=8, max_length=128)]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class RegisterIn(BaseModel):
    email: EmailStr
    password: Password
    display_name: DisplayName


class LoginIn(BaseModel):
    email: EmailStr
    # No length rules on login: a wrong password is just a wrong password.
    password: Annotated[str, StringConstraints(max_length=1024)]


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    role: Literal["user", "admin"]
    created_at: datetime
