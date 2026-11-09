from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    """Create an account. Validation of email and password strength lives in
    AuthService, so the messages are the same whichever client calls."""

    email: str = Field(..., min_length=3, max_length=320)
    password: str = Field(..., min_length=1, max_length=1024)
    display_name: str = Field("", max_length=200)
    # A profile id from before accounts existed, which this signup takes over
    # if it has no credentials yet. The browser kept it in localStorage.
    claim_user_id: str | None = Field(None, max_length=200)


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=1, max_length=320)
    password: str = Field(..., min_length=1, max_length=1024)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=1024)
    new_password: str = Field(..., min_length=1, max_length=1024)
