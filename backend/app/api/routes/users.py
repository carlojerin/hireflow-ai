from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session
from passlib.context import CryptContext

from backend.app.database.database import get_db
from backend.app.models.user import User
from backend.app.schemas.user import (
    UserCreate,
    LoginRequest,
    UserResponse,
    UserUpdate
)

from backend.app.services.auth.session_service import (
    create_session,
    generate_csrf_token,
    set_csrf_cookie,
    set_session_cookie,
)
from backend.app.api.deps import get_current_user, require_csrf, require_self


router = APIRouter(
    prefix="/api/users",
    tags=["Users"]
)


# ==========================================
# PASSWORD HASHING
# ==========================================

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)


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


# ==========================================
# REGISTER
# ==========================================

@router.post("/register")
def register_user(
    user_data: UserCreate,
    db: Session = Depends(get_db)
):

    # ------------------------------------------
    # PASSWORD CONFIRMATION
    # ------------------------------------------

    if user_data.password != user_data.confirm_password:
        raise HTTPException(
            status_code=400,
            detail="Passwords do not match"
        )


    # ------------------------------------------
    # USERNAME VALIDATION
    # ------------------------------------------

    username = user_data.username.strip()

    if not username:
        raise HTTPException(
            status_code=400,
            detail="Username is required"
        )

    if len(username) < 3:
        raise HTTPException(
            status_code=400,
            detail="Username must contain at least 3 characters"
        )


    # ------------------------------------------
    # USERNAME FORMAT
    # ------------------------------------------

    if not username.replace("_", "").isalnum():
        raise HTTPException(
            status_code=400,
            detail="Username can contain only letters, numbers and underscore"
        )


    # ------------------------------------------
    # CHECK USERNAME
    # ------------------------------------------

    existing_username = db.query(User).filter(
        User.username == username
    ).first()

    if existing_username:
        raise HTTPException(
            status_code=400,
            detail="Username already exists"
        )


    # ------------------------------------------
    # CHECK EMAIL
    # ------------------------------------------

    email = user_data.email.strip().lower()

    existing_email = db.query(User).filter(
        User.email == email
    ).first()

    if existing_email:
        raise HTTPException(
            status_code=400,
            detail="Email already registered"
        )


    # ------------------------------------------
    # MOBILE
    # ------------------------------------------

    mobile = None

    if user_data.mobile:
        mobile = user_data.mobile.strip()

        if mobile:

            existing_mobile = db.query(User).filter(
                User.mobile == mobile
            ).first()

            if existing_mobile:
                raise HTTPException(
                    status_code=400,
                    detail="Mobile number already registered"
                )

        else:
            mobile = None


    # ------------------------------------------
    # CREATE USER
    # ------------------------------------------

    user = User(

        # Account
        full_name=user_data.full_name.strip(),
        username=username,
        email=email,
        mobile=mobile,

        # Password
        hashed_password=hash_password(
            user_data.password
        ),

        # Basic onboarding
        location=getattr(
            user_data,
            "location",
            None
        ),

        employment_status=getattr(
            user_data,
            "employment_status",
            None
        ),

        current_role=getattr(
            user_data,
            "current_role",
            None
        ),

        industry=getattr(
            user_data,
            "industry",
            None
        ),

        education=getattr(
            user_data,
            "education",
            None
        ),

        graduation_year=getattr(
            user_data,
            "graduation_year",
            None
        ),

        experience_level=getattr(
            user_data,
            "experience_level",
            None
        ),

        # Career preference
        preferred_role=getattr(
            user_data,
            "preferred_role",
            None
        ),

        preferred_work_mode=getattr(
            user_data,
            "preferred_work_mode",
            None
        ),

        # Normal user account
        role="user"
    )


    # ------------------------------------------
    # SAVE
    # ------------------------------------------

    db.add(user)

    try:

        db.commit()
        db.refresh(user)

    except Exception:

        db.rollback()

        raise HTTPException(
            status_code=400,
            detail="Unable to create account. Please try again."
        )


    # ------------------------------------------
    # RESPONSE
    # ------------------------------------------

    return {

        "message": "Registration successful",

        "user": {

            "id": user.id,

            "full_name": user.full_name,

            "username": user.username,

            "email": user.email,

            "mobile": user.mobile,

            "location": user.location,

            "employment_status": user.employment_status,

            "current_role": user.current_role,

            "industry": user.industry,

            "education": user.education,

            "graduation_year": user.graduation_year,

            "experience_level": user.experience_level,

            "preferred_role": user.preferred_role,

            "preferred_work_mode": user.preferred_work_mode,

            "role": user.role
        }
    }


# ==========================================
# LOGIN
# ==========================================

@router.post("/login")
def login_user(
    login_data: LoginRequest,
    response: Response,
    db: Session = Depends(get_db)
):

    identifier = login_data.identifier.strip()

    user = db.query(User).filter(
        (User.username == identifier) |
        (User.email == identifier.lower()) |
        (User.mobile == identifier)
    ).first()


    # ------------------------------------------
    # USER NOT FOUND
    # ------------------------------------------

    if not user:

        raise HTTPException(
            status_code=401,
            detail="Invalid username/email/mobile or password"
        )


    # ------------------------------------------
    # PASSWORD
    # ------------------------------------------

    if not verify_password(
        login_data.password,
        user.hashed_password
    ):

        raise HTTPException(
            status_code=401,
            detail="Invalid username/email/mobile or password"
        )


    # ------------------------------------------
    # ACCOUNT DEACTIVATED
    # ------------------------------------------

    if not user.is_active:

        raise HTTPException(
            status_code=403,
            detail="Your account has been deactivated. Please contact support."
        )


    # ------------------------------------------
    # CREATE SERVER-SIDE SESSION
    #
    # Additive only — the response body below is unchanged.
    # The frontend does not need to read this cookie itself;
    # the browser attaches it automatically on later requests
    # (once those requests opt into credentials, as login.js
    # now does).
    # ------------------------------------------

    raw_token, _ = create_session(
        db,
        subject_type="user",
        subject_id=user.id,
        role_at_login=user.role,
    )

    set_session_cookie(response, raw_token)
    set_csrf_cookie(response, generate_csrf_token())


    # ------------------------------------------
    # LOGIN RESPONSE
    # ------------------------------------------

    return {

        "message": "Login successful",

        "user": {

            "id": user.id,

            "full_name": user.full_name,

            "username": user.username,

            "email": user.email,

            "mobile": user.mobile,

            "location": user.location,

            "employment_status": user.employment_status,

            "current_role": user.current_role,

            "industry": user.industry,

            "education": user.education,

            "graduation_year": user.graduation_year,

            "experience_level": user.experience_level,

            "preferred_role": user.preferred_role,

            "preferred_work_mode": user.preferred_work_mode,

            "role": user.role
        }
    }


# ==========================================
# GET USER
# ==========================================

@router.get("/{user_id}")
def get_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    require_self(current_user, user_id)

    user = db.query(User).filter(
        User.id == user_id
    ).first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    return {
        "message": "User retrieved successfully",
        "user": {
            "id": user.id,
            "full_name": user.full_name,
            "username": user.username,
            "email": user.email,
            "mobile": user.mobile,
            "location": user.location,
            "employment_status": user.employment_status,
            "current_role": user.current_role,
            "industry": user.industry,
            "education": user.education,
            "graduation_year": user.graduation_year,
            "experience_level": user.experience_level,
            "preferred_role": user.preferred_role,
            "preferred_work_mode": user.preferred_work_mode,
            "role": user.role
        }
    }


# ==========================================
# UPDATE BASIC INFORMATION
# ==========================================

@router.put("/{user_id}")
def update_user(
    user_id: int,
    user_data: UserUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    require_self(current_user, user_id)
    require_csrf(request)

    # ------------------------------------------
    # FIND USER
    # ------------------------------------------

    user = db.query(User).filter(
        User.id == user_id
    ).first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )


    # ------------------------------------------
    # CLEAN VALUES
    # ------------------------------------------

    full_name = user_data.full_name.strip()
    username = user_data.username.strip()
    email = user_data.email.strip().lower()

    mobile = None

    if user_data.mobile:
        mobile = user_data.mobile.strip()

        if mobile == "":
            mobile = None


    # ------------------------------------------
    # VALIDATE FULL NAME
    # ------------------------------------------

    if len(full_name) < 2:
        raise HTTPException(
            status_code=400,
            detail="Full name must contain at least 2 characters"
        )


    # ------------------------------------------
    # VALIDATE USERNAME
    # ------------------------------------------

    if not username:
        raise HTTPException(
            status_code=400,
            detail="Username is required"
        )

    if len(username) < 3:
        raise HTTPException(
            status_code=400,
            detail="Username must contain at least 3 characters"
        )

    if not username.replace("_", "").isalnum():
        raise HTTPException(
            status_code=400,
            detail="Username can contain only letters, numbers and underscore"
        )


    # ------------------------------------------
    # CHECK USERNAME DUPLICATE
    # ------------------------------------------

    existing_username = db.query(User).filter(
        User.username == username,
        User.id != user_id
    ).first()

    if existing_username:
        raise HTTPException(
            status_code=400,
            detail="Username already exists"
        )


    # ------------------------------------------
    # CHECK EMAIL DUPLICATE
    # ------------------------------------------

    existing_email = db.query(User).filter(
        User.email == email,
        User.id != user_id
    ).first()

    if existing_email:
        raise HTTPException(
            status_code=400,
            detail="Email already registered"
        )


    # ------------------------------------------
    # CHECK MOBILE DUPLICATE
    # ------------------------------------------

    if mobile:

        existing_mobile = db.query(User).filter(
            User.mobile == mobile,
            User.id != user_id
        ).first()

        if existing_mobile:
            raise HTTPException(
                status_code=400,
                detail="Mobile number already registered"
            )


    # ------------------------------------------
    # UPDATE USER
    # ------------------------------------------

    user.full_name = full_name
    user.username = username
    user.email = email
    user.mobile = mobile

    user.location = (
        user_data.location.strip()
        if user_data.location
        else None
    )

    user.employment_status = (
        user_data.employment_status.strip()
        if user_data.employment_status
        else None
    )

    user.current_role = (
        user_data.current_role.strip()
        if user_data.current_role
        else None
    )

    user.industry = (
        user_data.industry.strip()
        if user_data.industry
        else None
    )

    user.education = (
        user_data.education.strip()
        if user_data.education
        else None
    )

    user.graduation_year = (
        user_data.graduation_year
    )

    user.experience_level = (
        user_data.experience_level.strip()
        if user_data.experience_level
        else None
    )


    # ------------------------------------------
    # SAVE
    # ------------------------------------------

    try:

        db.commit()
        db.refresh(user)

    except Exception:

        db.rollback()

        raise HTTPException(
            status_code=400,
            detail="Unable to update basic information"
        )


    # ------------------------------------------
    # RESPONSE
    # ------------------------------------------

    return {

        "message": "Basic information updated successfully",

        "user": {

            "id": user.id,

            "full_name": user.full_name,

            "username": user.username,

            "email": user.email,

            "mobile": user.mobile,

            "location": user.location,

            "employment_status": user.employment_status,

            "current_role": user.current_role,

            "industry": user.industry,

            "education": user.education,

            "graduation_year": user.graduation_year,

            "experience_level": user.experience_level,

            "preferred_role": user.preferred_role,

            "preferred_work_mode": user.preferred_work_mode,

            "role": user.role
        }
    }