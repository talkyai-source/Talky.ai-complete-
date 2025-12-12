"""
API Dependencies
Shared dependencies for authentication, Supabase access, and authorization
"""
import os
from typing import Optional
from fastapi import Depends, HTTPException, status, Header
from supabase import create_client, Client
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()


class CurrentUser(BaseModel):
    """Current authenticated user model"""
    id: str
    email: str
    name: Optional[str] = None
    business_name: Optional[str] = None
    tenant_id: Optional[str] = None
    role: str = "user"
    minutes_remaining: int = 0


def get_supabase() -> Client:
    """
    Get Supabase client with validation.
    
    Raises:
        RuntimeError: If Supabase URL or SERVICE_KEY is not configured
    """
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    
    if not url:
        raise RuntimeError(
            "SUPABASE_URL is not configured. "
            "Set SUPABASE_URL environment variable."
        )
    if not key:
        raise RuntimeError(
            "SUPABASE_SERVICE_KEY is not configured. "
            "Set SUPABASE_SERVICE_KEY environment variable."
        )
    
    return create_client(url, key)


async def get_current_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    supabase: Client = Depends(get_supabase)
) -> CurrentUser:
    """
    Dependency to get the current authenticated user from JWT token.
    
    Args:
        authorization: Bearer token from Authorization header
        supabase: Supabase client
    
    Returns:
        CurrentUser object with user details
    
    Raises:
        HTTPException: If token is invalid or user not found
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Extract token from "Bearer <token>"
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Use: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = parts[1]
    
    try:
        # Verify token with Supabase
        user_response = supabase.auth.get_user(token)
        
        if not user_response or not user_response.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        auth_user = user_response.user
        
        # Get user profile from our user_profiles table
        profile_response = supabase.table("user_profiles").select(
            "*, tenants(business_name, minutes_allocated, minutes_used)"
        ).eq("id", auth_user.id).single().execute()
        
        if profile_response.data:
            profile = profile_response.data
            tenant = profile.get("tenants", {}) or {}
            minutes_remaining = tenant.get("minutes_allocated", 0) - tenant.get("minutes_used", 0)
            
            return CurrentUser(
                id=str(auth_user.id),
                email=auth_user.email,
                name=profile.get("name"),
                business_name=tenant.get("business_name"),
                tenant_id=profile.get("tenant_id"),
                role=profile.get("role", "user"),
                minutes_remaining=max(0, minutes_remaining)
            )
        else:
            # User exists in auth but no profile yet
            return CurrentUser(
                id=str(auth_user.id),
                email=auth_user.email,
                role="user",
                minutes_remaining=0
            )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token validation failed: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_admin(
    current_user: CurrentUser = Depends(get_current_user)
) -> CurrentUser:
    """
    Dependency to require admin role.
    
    Args:
        current_user: Current authenticated user
    
    Returns:
        CurrentUser if they are an admin
    
    Raises:
        HTTPException: If user is not an admin
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user


def get_optional_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    supabase: Client = Depends(get_supabase)
) -> Optional[CurrentUser]:
    """
    Dependency to optionally get current user (for endpoints that work with or without auth).
    
    Returns None if no valid token provided.
    """
    if not authorization:
        return None
    
    try:
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        
        token = parts[1]
        user_response = supabase.auth.get_user(token)
        
        if not user_response or not user_response.user:
            return None
        
        auth_user = user_response.user
        
        profile_response = supabase.table("user_profiles").select(
            "*, tenants(business_name, minutes_allocated, minutes_used)"
        ).eq("id", auth_user.id).single().execute()
        
        if profile_response.data:
            profile = profile_response.data
            tenant = profile.get("tenants", {}) or {}
            minutes_remaining = tenant.get("minutes_allocated", 0) - tenant.get("minutes_used", 0)
            
            return CurrentUser(
                id=str(auth_user.id),
                email=auth_user.email,
                name=profile.get("name"),
                business_name=tenant.get("business_name"),
                tenant_id=profile.get("tenant_id"),
                role=profile.get("role", "user"),
                minutes_remaining=max(0, minutes_remaining)
            )
        
        return CurrentUser(
            id=str(auth_user.id),
            email=auth_user.email,
            role="user",
            minutes_remaining=0
        )
    except Exception:
        return None
