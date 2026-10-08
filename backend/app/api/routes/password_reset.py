import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from passlib.context import CryptContext

from backend.app.database.database import get_db
from backend.app.models.user import User
from backend.app.models.password_reset import PasswordResetOTP
from backend.app.schemas.password_reset import (
    ForgotPasswordRequest,
    VerifyOTPRequest,
    ResetPasswordRequest
)
from backend.app.services.email.mailer import send_email


router = APIRouter(
    prefix="/api/password",
    tags=["Password Recovery"]
)

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)


def hash_otp(otp: str) -> str:
    return hashlib.sha256(
        otp.encode()
    ).hexdigest()


def find_user(
    identifier: str,
    db: Session
):
    return db.query(User).filter(
        (User.email == identifier) |
        (User.mobile == identifier)
    ).first()


def send_password_reset_email(
    to_email: str | None,
    full_name: str | None,
    otp: str,
) -> bool:
    """
    Deliver the OTP by email using the existing SMTP sender
    (services/email/mailer.py) — the only place this OTP value
    is allowed to travel to. send_email() is best-effort: if
    SMTP is not configured (as in local development today), it
    safely returns False without raising and without logging
    the OTP itself.
    """

    subject = "Your JobPilot AI Password Reset Code"

    name = full_name or "there"

    body = (
        f"Hi {name},\n\n"
        "We received a request to reset your JobPilot AI "
        "account password.\n\n"
        f"Your password reset code is: {otp}\n\n"
        "This code expires in 5 minutes. If you did not "
        "request this, you can safely ignore this email — "
        "your password will not be changed.\n\n"
        "— JobPilot AI"
    )

    return send_email(
        to_email,
        subject,
        body,
    )


# The generic response returned for a forgot-password request
# regardless of whether the account exists — this is the only
# thing STEP 1 (below) is ever allowed to return, so it can
# never depend on which branch of that check actually ran.
FORGOT_PASSWORD_RESPONSE = {
    "message": (
        "If an account exists for this email or mobile "
        "number, a password reset code has been sent."
    )
}


@router.post("/forgot")
def forgot_password(
    request: ForgotPasswordRequest,
    db: Session = Depends(get_db)
):

    user = find_user(
        request.identifier,
        db
    )

    # --------------------------------------------------------
    # STEP 1 — ACCOUNT ENUMERATION PROTECTION
    #
    # No account for this identifier: return the exact same
    # response as the success path below, so a caller can never
    # tell from the response alone whether an account exists.
    # --------------------------------------------------------

    if not user:
        return FORGOT_PASSWORD_RESPONSE

    # Invalidate previous unused OTPs
    previous_otps = db.query(
        PasswordResetOTP
    ).filter(
        PasswordResetOTP.user_id == user.id,
        PasswordResetOTP.used == False
    ).all()

    for old_otp in previous_otps:
        old_otp.used = True

    # Generate secure 6-digit OTP
    otp = f"{secrets.randbelow(1000000):06d}"

    otp_record = PasswordResetOTP(
        user_id=user.id,
        otp_hash=hash_otp(otp),
        expires_at=datetime.utcnow() + timedelta(
            minutes=5
        ),
        used=False
    )

    db.add(otp_record)
    db.commit()

    # --------------------------------------------------------
    # SECURITY: the OTP must never be returned to the caller —
    # it is only ever handed to send_password_reset_email(),
    # which is the sole path it can leave this process through.
    # Its delivery success/failure is intentionally not
    # reflected in the response either, for the same
    # enumeration-protection reason as above.
    # --------------------------------------------------------

    send_password_reset_email(
        to_email=user.email,
        full_name=user.full_name,
        otp=otp,
    )

    return FORGOT_PASSWORD_RESPONSE


@router.post("/verify-otp")
def verify_otp(
    request: VerifyOTPRequest,
    db: Session = Depends(get_db)
):

    user = find_user(
        request.identifier,
        db
    )

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    otp_record = db.query(
        PasswordResetOTP
    ).filter(
        PasswordResetOTP.user_id == user.id,
        PasswordResetOTP.used == False
    ).order_by(
        PasswordResetOTP.id.desc()
    ).first()

    if not otp_record:
        raise HTTPException(
            status_code=400,
            detail="No active OTP found"
        )

    if datetime.utcnow() > otp_record.expires_at:
        otp_record.used = True
        db.commit()

        raise HTTPException(
            status_code=400,
            detail="OTP has expired"
        )

    if hash_otp(request.otp) != otp_record.otp_hash:
        raise HTTPException(
            status_code=400,
            detail="Invalid OTP"
        )

    return {
        "message": "OTP verified successfully"
    }


@router.post("/reset")
def reset_password(
    request: ResetPasswordRequest,
    db: Session = Depends(get_db)
):

    if request.new_password != request.confirm_password:
        raise HTTPException(
            status_code=400,
            detail="Passwords do not match"
        )

    user = find_user(
        request.identifier,
        db
    )

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    otp_record = db.query(
        PasswordResetOTP
    ).filter(
        PasswordResetOTP.user_id == user.id,
        PasswordResetOTP.used == False
    ).order_by(
        PasswordResetOTP.id.desc()
    ).first()

    if not otp_record:
        raise HTTPException(
            status_code=400,
            detail="No active OTP found"
        )

    if datetime.utcnow() > otp_record.expires_at:
        otp_record.used = True
        db.commit()

        raise HTTPException(
            status_code=400,
            detail="OTP has expired"
        )

    if hash_otp(request.otp) != otp_record.otp_hash:
        raise HTTPException(
            status_code=400,
            detail="Invalid OTP"
        )

    user.hashed_password = pwd_context.hash(
        request.new_password
    )

    otp_record.used = True

    db.commit()

    return {
        "message": "Password reset successfully"
    }