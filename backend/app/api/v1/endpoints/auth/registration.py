"""Retired single-step signup. Canonical signup verifies the inbox first."""
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["auth"])


@router.post("/register", deprecated=True)
async def register(request: Request):
    # Do not create a user, tenant, membership, verification email or session.
    # A redirect cannot safely replay the old password-bearing request into
    # the canonical two-step flow, whose first step accepts no password.
    raise HTTPException(status_code=410, detail={
        "code": "registration_flow_retired",
        "message": "Use verified signup: start at /auth/signup/start, then complete email verification and /auth/signup/complete.",
        "signup_start": "/api/v1/auth/signup/start",
    })
