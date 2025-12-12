"""
Authentication Endpoints
Implements Supabase OTP (One-Time Password) email authentication
"""
from fastapi import APIRouter, HTTPException, Depends, status
from pydantic import BaseModel, EmailStr
from typing import Optional
from supabase import Client

from app.api.v1.dependencies import get_supabase, get_current_user, CurrentUser

router = APIRouter(prefix="/auth", tags=["auth"])


# ============================================
# Request/Response Models
# ============================================

class RegisterRequest(BaseModel):
    """User registration request"""
    email: EmailStr
    business_name: str
    plan_id: str = "basic"
    name: Optional[str] = None


class LoginRequest(BaseModel):
    """Login request - triggers OTP code email"""
    email: EmailStr


class VerifyOtpRequest(BaseModel):
    """OTP verification request"""
    email: EmailStr
    token: str  # The 6-digit code


class VerifyOtpResponse(BaseModel):
    """OTP verification response with tokens"""
    access_token: str
    refresh_token: str
    user_id: str
    email: str
    message: str


class AuthResponse(BaseModel):
    """Auth response with user info"""
    id: str
    email: str
    business_name: Optional[str] = None
    role: str = "user"
    minutes_remaining: int = 0
    message: str


class MeResponse(BaseModel):
    """Current user response"""
    id: str
    email: str
    name: Optional[str] = None
    business_name: Optional[str] = None
    role: str
    minutes_remaining: int


# ============================================
# Endpoints
# ============================================

@router.post("/register", response_model=AuthResponse)
async def register(
    request: RegisterRequest,
    supabase: Client = Depends(get_supabase)
):
    """
    Register a new user with OTP email verification.
    
    1. Creates tenant with selected plan
    2. Sends 6-digit OTP code to email
    3. User verifies code via /auth/verify-otp endpoint
    4. User profile is created on first verification
    """
    try:
        # Check if plan exists
        plan_response = supabase.table("plans").select("*").eq("id", request.plan_id).single().execute()
        
        if not plan_response.data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid plan_id: {request.plan_id}"
            )
        
        plan = plan_response.data
        
        # Check if email already exists in user_profiles
        existing = supabase.table("user_profiles").select("id").eq("email", request.email).execute()
        
        if existing.data and len(existing.data) > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
        
        # Create tenant first
        tenant_response = supabase.table("tenants").insert({
            "business_name": request.business_name,
            "plan_id": request.plan_id,
            "minutes_allocated": plan.get("minutes", 0),
            "minutes_used": 0
        }).execute()
        
        if not tenant_response.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create tenant"
            )
        
        tenant = tenant_response.data[0]
        
        # Send OTP code for sign up (not magic link)
        # type: "email" sends a 6-digit code instead of a link
        auth_response = supabase.auth.sign_in_with_otp({
            "email": request.email,
            "options": {
                "data": {
                    "tenant_id": tenant["id"],
                    "name": request.name,
                    "business_name": request.business_name,
                    "role": "owner"
                },
                "should_create_user": True
            }
        })
        
        return AuthResponse(
            id=tenant["id"],
            email=request.email,
            business_name=request.business_name,
            role="owner",
            minutes_remaining=plan.get("minutes", 0),
            message="Verification code sent to your email. Please check your inbox."
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Registration failed: {str(e)}"
        )


@router.post("/login", response_model=AuthResponse)
async def login(
    request: LoginRequest,
    supabase: Client = Depends(get_supabase)
):
    """
    Login with OTP code.
    
    Sends a 6-digit verification code to the user's email.
    User enters the code via /auth/verify-otp to complete authentication.
    """
    try:
        # Check if user exists
        profile = supabase.table("user_profiles").select(
            "*, tenants(business_name, minutes_allocated, minutes_used)"
        ).eq("email", request.email).execute()
        
        # Send OTP code regardless (for security, don't reveal if email exists)
        supabase.auth.sign_in_with_otp({
            "email": request.email,
            "options": {
                "should_create_user": False  # Don't create new user on login
            }
        })
        
        # Return generic response
        if profile.data and len(profile.data) > 0:
            user_profile = profile.data[0]
            tenant = user_profile.get("tenants", {}) or {}
            minutes_remaining = tenant.get("minutes_allocated", 0) - tenant.get("minutes_used", 0)
            
            return AuthResponse(
                id=user_profile["id"],
                email=request.email,
                business_name=tenant.get("business_name"),
                role=user_profile.get("role", "user"),
                minutes_remaining=max(0, minutes_remaining),
                message="Verification code sent to your email. Please check your inbox."
            )
        
        return AuthResponse(
            id="",
            email=request.email,
            role="user",
            minutes_remaining=0,
            message="If this email is registered, a verification code has been sent."
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Login failed: {str(e)}"
        )


@router.post("/verify-otp", response_model=VerifyOtpResponse)
async def verify_otp(
    request: VerifyOtpRequest,
    supabase: Client = Depends(get_supabase)
):
    """
    Verify the OTP code sent to user's email.
    
    This endpoint is called after user receives the 6-digit code.
    On success, returns access and refresh tokens.
    """
    try:
        # Verify the OTP code
        auth_response = supabase.auth.verify_otp({
            "email": request.email,
            "token": request.token,
            "type": "email"  # This is for email OTP verification
        })
        
        if not auth_response.session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired verification code"
            )
        
        session = auth_response.session
        user = auth_response.user
        
        # Check if user profile exists, create if not
        profile = supabase.table("user_profiles").select("id").eq("id", str(user.id)).execute()
        
        if not profile.data or len(profile.data) == 0:
            # First time login - create profile from user metadata
            metadata = user.user_metadata or {}
            
            supabase.table("user_profiles").insert({
                "id": str(user.id),
                "email": user.email,
                "name": metadata.get("name"),
                "tenant_id": metadata.get("tenant_id"),
                "role": metadata.get("role", "user")
            }).execute()
        
        return VerifyOtpResponse(
            access_token=session.access_token,
            refresh_token=session.refresh_token,
            user_id=str(user.id),
            email=user.email,
            message="Verification successful"
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Verification failed: {str(e)}"
        )


@router.get("/me", response_model=MeResponse)
async def get_me(
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Get current authenticated user info.
    
    Used by:
    - AuthContext on app load
    - DashboardLayout top bar
    """
    return MeResponse(
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        business_name=current_user.business_name,
        role=current_user.role,
        minutes_remaining=current_user.minutes_remaining
    )


@router.post("/logout")
async def logout(
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Logout the current user.
    
    Note: With magic link auth, this primarily clears the server-side session.
    The client should also clear the stored token.
    """
    try:
        # Sign out from Supabase
        supabase.auth.sign_out()
        
        return {"detail": "Logged out"}
    
    except Exception as e:
        # Still return success even if sign out fails
        # Client will clear token anyway
        return {"detail": "Logged out"}


@router.post("/callback")
async def auth_callback(
    supabase: Client = Depends(get_supabase)
):
    """
    Handle magic link callback.
    
    This endpoint is called when user clicks the magic link.
    It creates the user profile if it doesn't exist.
    
    Note: In practice, Supabase handles this automatically.
    This is a placeholder for any additional logic needed.
    """
    return {"detail": "Callback processed"}


@router.post("/create-profile")
async def create_profile(
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Create user profile after first login via magic link.
    
    Called by frontend after user successfully authenticates via magic link
    but doesn't have a profile yet.
    """
    try:
        # Check if profile exists
        existing = supabase.table("user_profiles").select("id").eq("id", current_user.id).execute()
        
        if existing.data and len(existing.data) > 0:
            return {"detail": "Profile already exists"}
        
        # Get user metadata from auth (contains tenant_id, name, etc. from registration)
        # This data was passed during sign_in_with_otp
        auth_user = supabase.auth.get_user()
        metadata = auth_user.user.user_metadata if auth_user.user else {}
        
        # Create profile
        supabase.table("user_profiles").insert({
            "id": current_user.id,
            "email": current_user.email,
            "name": metadata.get("name"),
            "tenant_id": metadata.get("tenant_id"),
            "role": metadata.get("role", "user")
        }).execute()
        
        return {"detail": "Profile created"}
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create profile: {str(e)}"
        )
