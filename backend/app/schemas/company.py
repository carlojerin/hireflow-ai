from pydantic import BaseModel, EmailStr, Field, ConfigDict


# ============================================================
# COMPANY REGISTRATION
# ============================================================

class CompanyCreate(BaseModel):

    company_name: str = Field(
        min_length=2,
        max_length=200
    )

    hr_name: str = Field(
        min_length=2,
        max_length=150
    )

    legal_name: str | None = Field(
        default=None,
        max_length=250
    )

    email: EmailStr

    mobile: str = Field(
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

    company_type: str | None = Field(
        default=None,
        max_length=100
    )

    industry: str | None = Field(
        default=None,
        max_length=150
    )

    website: str | None = Field(
        default=None,
        max_length=500
    )

    location: str | None = Field(
        default=None,
        max_length=200
    )

    description: str | None = Field(
        default=None,
        max_length=5000
    )

    # Business identifiers
    cin: str | None = Field(
        default=None,
        max_length=50
    )

    llpin: str | None = Field(
        default=None,
        max_length=50
    )

    gstin: str | None = Field(
        default=None,
        max_length=50
    )

    # Company information
    company_size: str | None = Field(
        default=None,
        max_length=100
    )

    founded_year: int | None = Field(
        default=None,
        ge=1800,
        le=2100
    )


# ============================================================
# COMPANY LOGIN
# ============================================================

class CompanyLoginRequest(BaseModel):

    identifier: str

    password: str


# ============================================================
# COMPANY RESPONSE
# ============================================================

class CompanyResponse(BaseModel):

    model_config = ConfigDict(
        from_attributes=True
    )

    id: int

    company_name: str

    hr_name: str | None = None

    username: str | None = None

    legal_name: str

    email: EmailStr

    mobile: str

    company_type: str | None = None

    industry: str | None = None

    website: str | None = None

    location: str | None = None

    description: str | None = None

    cin: str | None = None

    llpin: str | None = None

    gstin: str | None = None

    company_size: str | None = None

    founded_year: int | None = None

    verification_status: str

    account_active: bool

    email_verified: bool

    mobile_verified: bool

    admin_verified: bool

    company_logo: str | None = None

    role: str


# ============================================================
# COMPANY ACCOUNT SETTINGS (email / username)
# ============================================================

class CompanyAccountUpdate(BaseModel):

    email: EmailStr | None = None

    username: str | None = Field(
        default=None,
        min_length=3,
        max_length=80
    )


# ============================================================
# COMPANY PASSWORD CHANGE
# ============================================================

class CompanyPasswordChange(BaseModel):

    current_password: str = Field(min_length=1, max_length=128)

    new_password: str = Field(min_length=8, max_length=128)