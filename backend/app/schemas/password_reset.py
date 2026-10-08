from pydantic import BaseModel, Field


class ForgotPasswordRequest(BaseModel):
    identifier: str = Field(
        min_length=3,
        max_length=255
    )


class VerifyOTPRequest(BaseModel):
    identifier: str
    otp: str = Field(
        min_length=6,
        max_length=6
    )


class ResetPasswordRequest(BaseModel):
    identifier: str
    otp: str = Field(
        min_length=6,
        max_length=6
    )
    new_password: str = Field(
        min_length=8,
        max_length=128
    )
    confirm_password: str = Field(
        min_length=8,
        max_length=128
    )