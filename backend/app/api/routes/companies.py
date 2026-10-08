import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from passlib.context import CryptContext

from backend.app.database.database import get_db
from backend.app.models.company import Company
from backend.app.schemas.company import (
    CompanyCreate,
    CompanyLoginRequest,
    CompanyAccountUpdate,
    CompanyPasswordChange,
)

from backend.app.services.auth.session_service import (
    create_session,
    generate_csrf_token,
    set_csrf_cookie,
    set_session_cookie,
    COMPANY_SESSION_COOKIE_NAME,
)
from backend.app.services.auth.username import generate_unique_username
from backend.app.api.deps import get_current_company, require_company_self, require_csrf


router = APIRouter(
    prefix="/api/companies",
    tags=["Companies"]
)


pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)


# =========================================================
# COMPANY LOGO STORAGE
# =========================================================

# companies.py is:
# backend/app/api/routes/companies.py
#
# parents[3] = backend
#
# Therefore uploads are stored in:
# backend/uploads/company_logos

BACKEND_DIRECTORY = Path(__file__).resolve().parents[3]

UPLOADS_DIRECTORY = (
    BACKEND_DIRECTORY / "uploads"
)

COMPANY_LOGOS_DIRECTORY = (
    UPLOADS_DIRECTORY / "company_logos"
)

COMPANY_LOGOS_DIRECTORY.mkdir(
    parents=True,
    exist_ok=True
)


# Legacy location created by the previous version.
# We keep this temporarily so existing uploaded logos
# can still be found and removed/moved correctly.

LEGACY_UPLOADS_DIRECTORY = (
    Path(__file__).resolve().parents[2] / "uploads"
)

LEGACY_COMPANY_LOGOS_DIRECTORY = (
    LEGACY_UPLOADS_DIRECTORY / "company_logos"
)

LEGACY_COMPANY_LOGOS_DIRECTORY.mkdir(
    parents=True,
    exist_ok=True
)


ALLOWED_LOGO_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


MAX_LOGO_SIZE = 5 * 1024 * 1024


# =========================================================
# PASSWORD HELPERS
# =========================================================

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(
    plain_password: str,
    hashed_password: str
) -> bool:
    return pwd_context.verify(
        plain_password,
        hashed_password
    )


# =========================================================
# RESPONSE HELPER
# =========================================================

def company_response(company: Company) -> dict:
    return {
        "id": company.id,
        "company_name": company.company_name,
        "hr_name": company.hr_name,
        "username": company.username,
        "legal_name": company.legal_name,
        "email": company.email,
        "mobile": company.mobile,
        "company_type": company.company_type,
        "industry": company.industry,
        "website": company.website,
        "location": company.location,
        "description": company.description,
        "cin": company.cin,
        "llpin": company.llpin,
        "gstin": company.gstin,
        "company_size": company.company_size,
        "founded_year": company.founded_year,
        "verification_status": company.verification_status,
        "account_active": company.account_active,
        "email_verified": company.email_verified,
        "mobile_verified": company.mobile_verified,
        "admin_verified": company.admin_verified,
        "company_logo": company.company_logo,
        "role": company.role
    }


# =========================================================
# PROFILE UPDATE SCHEMA
# =========================================================

class CompanyProfileUpdate(BaseModel):

    company_name: str = Field(
        min_length=2,
        max_length=255
    )

    hr_name: str | None = Field(
        default=None,
        max_length=150
    )

    legal_name: str | None = Field(
        default=None,
        max_length=255
    )

    company_type: str | None = Field(
        default=None,
        max_length=100
    )

    industry: str | None = Field(
        default=None,
        max_length=255
    )

    website: str | None = Field(
        default=None,
        max_length=500
    )

    location: str | None = Field(
        default=None,
        max_length=255
    )

    company_size: str | None = Field(
        default=None,
        max_length=100
    )

    founded_year: int | None = Field(
        default=None,
        ge=1800,
        le=2100
    )

    description: str | None = Field(
        default=None,
        max_length=5000
    )


def clean_optional_value(
    value: str | None
) -> str | None:

    if value is None:
        return None

    cleaned = value.strip()

    return cleaned if cleaned else None


# =========================================================
# LOGO FILE HELPERS
# =========================================================

def get_logo_filename(
    logo_path: str | None
) -> str | None:

    if not logo_path:
        return None

    prefix = "/uploads/company_logos/"

    if not logo_path.startswith(prefix):
        return None

    return logo_path[len(prefix):]


def get_logo_file_path(
    logo_path: str | None
) -> Path | None:

    filename = get_logo_filename(
        logo_path
    )

    if not filename:
        return None

    current_path = (
        COMPANY_LOGOS_DIRECTORY /
        filename
    )

    if current_path.exists():
        return current_path

    legacy_path = (
        LEGACY_COMPANY_LOGOS_DIRECTORY /
        filename
    )

    if legacy_path.exists():
        return legacy_path

    return current_path


def migrate_legacy_logo(
    logo_path: str | None
) -> None:

    filename = get_logo_filename(
        logo_path
    )

    if not filename:
        return

    current_path = (
        COMPANY_LOGOS_DIRECTORY /
        filename
    )

    if current_path.exists():
        return

    legacy_path = (
        LEGACY_COMPANY_LOGOS_DIRECTORY /
        filename
    )

    if not legacy_path.exists():
        return

    try:

        current_path.write_bytes(
            legacy_path.read_bytes()
        )

        legacy_path.unlink()

    except Exception as error:

        print(
            "Unable to migrate legacy company logo:",
            error
        )


# =========================================================
# HR EMAIL VERIFICATION
#
# Registration emails a 6-digit code (valid 15 minutes, 5 attempts, a new one at most once a minute). Login refuses an account until the code
# is entered. Only the hash of a code is stored.
# =========================================================

VERIFICATION_CODE_MINUTES = 15
VERIFICATION_MAX_ATTEMPTS = 5
VERIFICATION_RESEND_SECONDS = 60


class EmailVerifyRequest(BaseModel):
    email: str
    code: str = Field(min_length=4, max_length=12)


class EmailResendRequest(BaseModel):
    email: str


def _hash_verification_code(company_id: int, code: str) -> str:
    return hashlib.sha256(f"{company_id}:{code}".encode()).hexdigest()


def _issue_verification_code(company: Company) -> str:
    """Create a fresh code on the company row (the caller commits) and return it so it can be emailed."""

    code = f"{secrets.randbelow(1_000_000):06d}"
    now = datetime.utcnow()

    company.email_verification_hash = _hash_verification_code(company.id, code)
    company.email_verification_expires_at = now + timedelta(minutes=VERIFICATION_CODE_MINUTES)
    company.email_verification_attempts = 0
    company.email_verification_sent_at = now

    return code


def _send_verification_email(company_email: str, hr_name: str | None, company_name: str | None, code: str) -> None:
    from backend.app.services.email.notifications import send_company_email_verification

    send_company_email_verification(company_email, hr_name, company_name, code)


@router.post("/verify-email")
def verify_company_email(payload: EmailVerifyRequest, db: Session = Depends(get_db)):

    email = payload.email.strip().lower()
    company = db.query(Company).filter(Company.email == email).first()

    generic = HTTPException(status_code=400, detail="That code is not correct or has expired. Request a new one.")

    if company is None:
        raise generic

    if company.email_verified:
        return {"message": "Your email is already verified. You can log in.", "verified": True}

    if not company.email_verification_hash or not company.email_verification_expires_at:
        raise generic

    if company.email_verification_attempts >= VERIFICATION_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many wrong codes. Request a new code to try again.")

    if datetime.utcnow() > company.email_verification_expires_at:
        raise generic

    company.email_verification_attempts += 1

    if not hmac.compare_digest(company.email_verification_hash, _hash_verification_code(company.id, payload.code.strip())):
        db.commit()
        raise generic

    company.email_verified = True
    company.email_verification_hash = None
    company.email_verification_expires_at = None
    company.email_verification_attempts = 0
    db.commit()

    return {"message": "Email verified. You can log in now.", "verified": True}


@router.post("/resend-verification")
def resend_company_verification(payload: EmailResendRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):

    email = payload.email.strip().lower()
    company = db.query(Company).filter(Company.email == email).first()

    # always the same reply: this never reveals whether an address has an account
    reply = {"message": "If that account needs verification, a new code is on its way.", "retry_after": VERIFICATION_RESEND_SECONDS}

    if company is None or company.email_verified:
        return reply

    now = datetime.utcnow()

    if company.email_verification_sent_at and (now - company.email_verification_sent_at).total_seconds() < VERIFICATION_RESEND_SECONDS:
        return reply

    code = _issue_verification_code(company)
    db.commit()

    background_tasks.add_task(_send_verification_email, company.email, company.hr_name, company.company_name, code)

    return reply


# =========================================================
# COMPANY REGISTRATION
# =========================================================

@router.post("/register")
def register_company(
    company_data: CompanyCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):

    if (
        company_data.password
        != company_data.confirm_password
    ):
        raise HTTPException(
            status_code=400,
            detail="Passwords do not match"
        )


    company_name = (
        company_data.company_name.strip()
    )

    hr_name = (
        company_data.hr_name.strip()
    )

    # Legal/registered name is no longer collected on the simplified
    # registration form -- the company's display name doubles as it
    # until (and unless) HR fills in the real one later.
    legal_name = (
        company_data.legal_name.strip()
        if company_data.legal_name
        else company_name
    )

    email = (
        company_data.email.strip().lower()
    )

    mobile = (
        company_data.mobile.strip()
    )


    if not company_name:
        raise HTTPException(
            status_code=400,
            detail="Company name is required"
        )


    if not hr_name:
        raise HTTPException(
            status_code=400,
            detail="Your name is required"
        )


    if not mobile:
        raise HTTPException(
            status_code=400,
            detail="Mobile number is required"
        )


    existing_email = (
        db.query(Company)
        .filter(Company.email == email)
        .first()
    )

    if existing_email:
        raise HTTPException(
            status_code=400,
            detail="Company email already registered"
        )


    existing_mobile = (
        db.query(Company)
        .filter(Company.mobile == mobile)
        .first()
    )

    if existing_mobile:
        raise HTTPException(
            status_code=400,
            detail="Company mobile number already registered"
        )


    cin = (
        company_data.cin.strip().upper()
        if company_data.cin
        else None
    )

    if cin:

        existing_cin = (
            db.query(Company)
            .filter(Company.cin == cin)
            .first()
        )

        if existing_cin:
            raise HTTPException(
                status_code=400,
                detail="CIN already registered"
            )


    llpin = (
        company_data.llpin.strip().upper()
        if company_data.llpin
        else None
    )

    if llpin:

        existing_llpin = (
            db.query(Company)
            .filter(Company.llpin == llpin)
            .first()
        )

        if existing_llpin:
            raise HTTPException(
                status_code=400,
                detail="LLPIN already registered"
            )


    gstin = (
        company_data.gstin.strip().upper()
        if company_data.gstin
        else None
    )

    if gstin:

        existing_gstin = (
            db.query(Company)
            .filter(Company.gstin == gstin)
            .first()
        )

        if existing_gstin:
            raise HTTPException(
                status_code=400,
                detail="GSTIN already registered"
            )


    company = Company(

        company_name=company_name,

        hr_name=hr_name,

        username=generate_unique_username(db, Company, "username", email),

        legal_name=legal_name,

        email=email,

        mobile=mobile,

        hashed_password=hash_password(
            company_data.password
        ),

        company_type=(
            company_data.company_type.strip()
            if company_data.company_type
            else None
        ),

        industry=(
            company_data.industry.strip()
            if company_data.industry
            else None
        ),

        website=(
            company_data.website.strip()
            if company_data.website
            else None
        ),

        location=(
            company_data.location.strip()
            if company_data.location
            else None
        ),

        description=(
            company_data.description.strip()
            if company_data.description
            else None
        ),

        cin=cin,

        llpin=llpin,

        gstin=gstin,

        company_size=(
            company_data.company_size.strip()
            if company_data.company_size
            else None
        ),

        founded_year=company_data.founded_year,

        # No admin panel exists to manually approve new HR accounts, so
        # registration verifies/activates immediately -- login only ever
        # checks these same three flags, so this is what makes the very
        # next login attempt succeed.
        verification_status="verified",

        account_active=True,

        email_verified=False,

        mobile_verified=False,

        admin_verified=True,

        company_logo=None,

        role="company"
    )


    db.add(company)


    try:

        db.commit()

        db.refresh(company)

    except Exception:

        db.rollback()

        raise HTTPException(
            status_code=400,
            detail="Unable to create company registration"
        )


    # the account exists but cannot log in until its email is verified: a 6-digit code goes out right after this reply
    code = _issue_verification_code(company)
    db.commit()

    background_tasks.add_task(_send_verification_email, company.email, company.hr_name, company.company_name, code)


    return {
        "message": "Account created. Enter the 6-digit code we emailed you to verify your email address.",
        "verification_required": True,
        "email": company.email,
        "company": company_response(company)
    }


# =========================================================
# COMPANY LOGIN
# =========================================================

@router.post("/login")
def login_company(
    login_data: CompanyLoginRequest,
    response: Response,
    db: Session = Depends(get_db)
):

    identifier = (
        login_data.identifier.strip()
    )


    company = (
        db.query(Company)
        .filter(
            Company.email
            == identifier.lower()
        )
        .first()
    )


    if not company:

        raise HTTPException(
            status_code=401,
            detail="Invalid company email or password"
        )


    if not verify_password(
        login_data.password,
        company.hashed_password
    ):

        raise HTTPException(
            status_code=401,
            detail="Invalid company email or password"
        )


    if (
        company.verification_status
        == "pending"
    ):

        raise HTTPException(
            status_code=403,
            detail=(
                "Your company registration "
                "is pending admin verification."
            )
        )


    if (
        company.verification_status
        == "rejected"
    ):

        raise HTTPException(
            status_code=403,
            detail=(
                "Your company registration was "
                "rejected. Please contact "
                "JobPilot support."
            )
        )


    if not company.account_active:

        raise HTTPException(
            status_code=403,
            detail=(
                "Your company account "
                "is not active yet."
            )
        )


    if not company.admin_verified:

        raise HTTPException(
            status_code=403,
            detail=(
                "Your company account is "
                "waiting for admin verification."
            )
        )


    # a correct password is not enough: the email address must be verified first (the page then asks for the emailed code)
    if not company.email_verified:

        raise HTTPException(
            status_code=403,
            detail="email_not_verified"
        )


    # Additive only — response body below is unchanged.
    raw_token, _ = create_session(
        db,
        subject_type="company",
        subject_id=company.id,
        role_at_login=company.role,
    )

    set_session_cookie(
        response, raw_token, cookie_name=COMPANY_SESSION_COOKIE_NAME,
    )
    set_csrf_cookie(response, generate_csrf_token())


    return {
        "message": "Company login successful",
        "company": company_response(company)
    }


# =========================================================
# GET COMPANY
# =========================================================

@router.get("/{company_id}")
def get_company(
    company_id: int,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db)
):

    require_company_self(current_company, company_id)

    company = (
        db.query(Company)
        .filter(
            Company.id == company_id
        )
        .first()
    )


    if not company:

        raise HTTPException(
            status_code=404,
            detail="Company not found"
        )


    # Move an already-uploaded legacy logo
    # into the correct backend/uploads folder.

    migrate_legacy_logo(
        company.company_logo
    )


    return {
        "message": "Company retrieved successfully",
        "company": company_response(company)
    }


# =========================================================
# UPDATE COMPANY PROFILE
# =========================================================

@router.put("/{company_id}/profile")
def update_company_profile(
    company_id: int,
    profile_data: CompanyProfileUpdate,
    request: Request,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db)
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    company = (
        db.query(Company)
        .filter(
            Company.id == company_id
        )
        .first()
    )


    if not company:

        raise HTTPException(
            status_code=404,
            detail="Company not found"
        )


    if (
        company.verification_status
        != "verified"
    ):

        raise HTTPException(
            status_code=403,
            detail=(
                "Only verified companies "
                "can update their profile."
            )
        )


    if not company.account_active:

        raise HTTPException(
            status_code=403,
            detail=(
                "Your company account "
                "is not active."
            )
        )


    if not company.admin_verified:

        raise HTTPException(
            status_code=403,
            detail=(
                "Your company account "
                "is not admin verified."
            )
        )


    company.company_name = (
        profile_data.company_name.strip()
    )

    company.hr_name = (
        clean_optional_value(profile_data.hr_name)
    )

    if profile_data.legal_name:
        company.legal_name = profile_data.legal_name.strip()

    company.company_type = (
        clean_optional_value(
            profile_data.company_type
        )
    )

    company.industry = (
        clean_optional_value(
            profile_data.industry
        )
    )

    company.website = (
        clean_optional_value(
            profile_data.website
        )
    )

    company.location = (
        clean_optional_value(
            profile_data.location
        )
    )

    company.company_size = (
        clean_optional_value(
            profile_data.company_size
        )
    )

    company.founded_year = (
        profile_data.founded_year
    )

    company.description = (
        clean_optional_value(
            profile_data.description
        )
    )


    try:

        db.commit()

        db.refresh(company)

    except Exception:

        db.rollback()

        raise HTTPException(
            status_code=400,
            detail=(
                "Unable to update company profile."
            )
        )


    return {
        "message": (
            "Company profile updated successfully."
        ),
        "company": company_response(company)
    }


# =========================================================
# UPDATE COMPANY ACCOUNT (email / username) -- Settings page
# =========================================================

@router.put("/{company_id}/account")
def update_company_account(
    company_id: int,
    account_data: CompanyAccountUpdate,
    request: Request,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db)
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    company = db.query(Company).filter(Company.id == company_id).first()

    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    if account_data.email is not None:

        new_email = account_data.email.strip().lower()

        existing = (
            db.query(Company)
            .filter(Company.email == new_email, Company.id != company_id)
            .first()
        )

        if existing:
            raise HTTPException(status_code=400, detail="That email is already in use")

        company.email = new_email

    if account_data.username is not None:

        new_username = account_data.username.strip()

        if not new_username:
            raise HTTPException(status_code=400, detail="Username cannot be empty")

        existing = (
            db.query(Company)
            .filter(Company.username == new_username, Company.id != company_id)
            .first()
        )

        if existing:
            raise HTTPException(status_code=400, detail="That username is already taken")

        company.username = new_username

    try:
        db.commit()
        db.refresh(company)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=400, detail="Unable to update account settings.")

    return {
        "message": "Account settings updated successfully.",
        "company": company_response(company)
    }


# =========================================================
# CHANGE COMPANY PASSWORD -- Settings page
# =========================================================

@router.post("/{company_id}/change-password")
def change_company_password(
    company_id: int,
    password_data: CompanyPasswordChange,
    request: Request,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db)
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    company = db.query(Company).filter(Company.id == company_id).first()

    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    if not verify_password(password_data.current_password, company.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    company.hashed_password = hash_password(password_data.new_password)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=400, detail="Unable to change password.")

    return {"message": "Password changed successfully."}


# =========================================================
# UPLOAD COMPANY LOGO
# =========================================================

@router.post("/{company_id}/logo")
async def upload_company_logo(
    company_id: int,
    request: Request,
    file: UploadFile = File(...),
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db)
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    company = (
        db.query(Company)
        .filter(
            Company.id == company_id
        )
        .first()
    )


    if not company:

        raise HTTPException(
            status_code=404,
            detail="Company not found"
        )


    if (
        company.verification_status
        != "verified"
    ):

        raise HTTPException(
            status_code=403,
            detail=(
                "Only verified companies "
                "can upload a company logo."
            )
        )


    if not company.account_active:

        raise HTTPException(
            status_code=403,
            detail=(
                "Your company account "
                "is not active."
            )
        )


    if not company.admin_verified:

        raise HTTPException(
            status_code=403,
            detail=(
                "Your company account "
                "is not admin verified."
            )
        )


    if file.content_type not in ALLOWED_LOGO_TYPES:

        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid logo format. "
                "Please upload JPG, PNG or WEBP."
            )
        )


    file_content = await file.read()


    if len(file_content) > MAX_LOGO_SIZE:

        raise HTTPException(
            status_code=400,
            detail=(
                "Logo size must be 5 MB or less."
            )
        )


    if len(file_content) == 0:

        raise HTTPException(
            status_code=400,
            detail="Uploaded logo is empty."
        )


    extension = ALLOWED_LOGO_TYPES[
        file.content_type
    ]


    filename = (
        f"company_{company.id}_"
        f"{uuid4().hex}"
        f"{extension}"
    )


    destination = (
        COMPANY_LOGOS_DIRECTORY /
        filename
    )


    # ---------------------------------------------
    # DELETE OLD LOGO
    # ---------------------------------------------

    old_logo = company.company_logo


    if old_logo:

        old_path = get_logo_file_path(
            old_logo
        )

        try:

            if old_path and old_path.exists():
                old_path.unlink()

        except Exception as error:

            print(
                "Unable to delete old company logo:",
                error
            )


    # ---------------------------------------------
    # SAVE NEW LOGO
    # ---------------------------------------------

    try:

        destination.write_bytes(
            file_content
        )

    except Exception:

        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to save company logo."
            )
        )


    # ---------------------------------------------
    # DATABASE PATH
    # ---------------------------------------------

    company.company_logo = (
        f"/uploads/company_logos/{filename}"
    )


    try:

        db.commit()

        db.refresh(company)

    except Exception:

        db.rollback()

        try:

            if destination.exists():
                destination.unlink()

        except Exception:
            pass

        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to update company logo."
            )
        )


    return {
        "message": (
            "Company logo uploaded successfully."
        ),
        "company": company_response(company)
    }


# =========================================================
# DELETE COMPANY LOGO
# =========================================================

@router.delete("/{company_id}/logo")
def delete_company_logo(
    company_id: int,
    request: Request,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db)
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    company = (
        db.query(Company)
        .filter(
            Company.id == company_id
        )
        .first()
    )


    if not company:

        raise HTTPException(
            status_code=404,
            detail="Company not found"
        )


    if (
        company.verification_status
        != "verified"
        or not company.account_active
        or not company.admin_verified
    ):

        raise HTTPException(
            status_code=403,
            detail=(
                "Your company account "
                "is not authorized for this action."
            )
        )


    old_logo = company.company_logo


    if old_logo:

        old_path = get_logo_file_path(
            old_logo
        )


        try:

            if old_path and old_path.exists():
                old_path.unlink()

        except Exception as error:

            print(
                "Unable to delete company logo:",
                error
            )


    company.company_logo = None


    try:

        db.commit()

        db.refresh(company)

    except Exception:

        db.rollback()

        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to remove company logo."
            )
        )


    return {
        "message": (
            "Company logo removed successfully."
        ),
        "company": company_response(company)
    }