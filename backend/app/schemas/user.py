from pydantic import BaseModel, EmailStr, Field, ConfigDict


# ============================================================
# USER REGISTRATION
# ============================================================

class UserCreate(BaseModel):

    full_name: str = Field(
        min_length=2,
        max_length=150
    )

    username: str = Field(
        min_length=3,
        max_length=100
    )

    email: EmailStr

    mobile: str | None = Field(
        default=None,
        min_length=10,
        max_length=20
    )

    password: str = Field(
        min_length=8,
        max_length=128
    )

    confirm_password: str = Field(
        min_length=8,
        max_length=128
    )

    location: str | None = Field(
        default=None,
        min_length=2,
        max_length=150
    )

    employment_status: str | None = Field(
        default=None,
        max_length=100
    )

    current_role: str | None = Field(
        default=None,
        max_length=150
    )

    industry: str | None = Field(
        default=None,
        max_length=150
    )

    education: str | None = Field(
        default=None,
        max_length=200
    )

    graduation_year: int | None = Field(
        default=None,
        ge=1950,
        le=2100
    )

    experience_level: str | None = Field(
        default=None,
        max_length=50
    )

    preferred_role: str | None = Field(
        default=None,
        max_length=150
    )

    preferred_work_mode: str | None = Field(
        default=None,
        max_length=50
    )


# ============================================================
# LOGIN
# ============================================================

class LoginRequest(BaseModel):

    identifier: str

    password: str


# ============================================================
# BASIC INFORMATION UPDATE
# ============================================================

class UserUpdate(BaseModel):

    full_name: str = Field(
        min_length=2,
        max_length=150
    )

    username: str = Field(
        min_length=3,
        max_length=100
    )

    email: EmailStr

    mobile: str | None = Field(
        default=None,
        min_length=10,
        max_length=20
    )

    location: str | None = Field(
        default=None,
        max_length=150
    )

    employment_status: str | None = Field(
        default=None,
        max_length=100
    )

    current_role: str | None = Field(
        default=None,
        max_length=150
    )

    industry: str | None = Field(
        default=None,
        max_length=150
    )

    education: str | None = Field(
        default=None,
        max_length=200
    )

    graduation_year: int | None = Field(
        default=None,
        ge=1950,
        le=2100
    )

    experience_level: str | None = Field(
        default=None,
        max_length=50
    )


# ============================================================
# USER RESPONSE
# ============================================================

class UserResponse(BaseModel):

    model_config = ConfigDict(
        from_attributes=True
    )

    id: int

    full_name: str

    username: str

    email: EmailStr

    mobile: str | None = None

    location: str | None = None

    employment_status: str | None = None

    current_role: str | None = None

    industry: str | None = None

    education: str | None = None

    graduation_year: int | None = None

    experience_level: str | None = None

    preferred_role: str | None = None

    preferred_work_mode: str | None = None

    role: str | None = None