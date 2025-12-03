"""
Multi-Tenant Middleware
INSTRUCTIONS: Uncomment all code in this file to enable multi-tenancy
"""
# from fastapi import Request, HTTPException, status
# from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
# from typing import Optional
# import jwt

# security = HTTPBearer()

# class TenantMiddleware:
#     """
#     Middleware to extract and validate tenant_id from JWT token
#     
#     Usage:
#     1. Uncomment all code in this file
#     2. Add to main.py: app.add_middleware(TenantMiddleware)
#     3. Uncomment tenant_id fields in all domain models
#     4. Uncomment tenant filtering in all endpoints
#     """
#     
#     async def __call__(self, request: Request, call_next):
#         # Skip tenant check for public endpoints
#         if request.url.path in ["/", "/health", "/docs", "/openapi.json"]:
#             return await call_next(request)
#         
#         # Extract JWT token from Authorization header
#         auth_header = request.headers.get("Authorization")
#         if not auth_header or not auth_header.startswith("Bearer "):
#             raise HTTPException(
#                 status_code=status.HTTP_401_UNAUTHORIZED,
#                 detail="Missing or invalid authorization header"
#             )
#         
#         token = auth_header.split(" ")[1]
#         
#         try:
#             # Decode JWT and extract tenant_id
#             payload = jwt.decode(
#                 token,
#                 options={"verify_signature": False}  # Configure with your secret
#             )
#             tenant_id = payload.get("tenant_id")
#             
#             if not tenant_id:
#                 raise HTTPException(
#                     status_code=status.HTTP_401_UNAUTHORIZED,
#                     detail="Token missing tenant_id"
#                 )
#             
#             # Attach tenant_id to request state
#             request.state.tenant_id = tenant_id
#             
#         except jwt.InvalidTokenError:
#             raise HTTPException(
#                 status_code=status.HTTP_401_UNAUTHORIZED,
#                 detail="Invalid token"
#             )
#         
#         return await call_next(request)


# def get_current_tenant(request: Request) -> str:
#     """
#     Dependency to get current tenant_id from request
#     
#     Usage in endpoints:
#     @router.get("/campaigns")
#     async def list_campaigns(
#         request: Request,
#         tenant_id: str = Depends(get_current_tenant)
#     ):
#         # Filter campaigns by tenant_id
#         campaigns = db.query(Campaign).filter(Campaign.tenant_id == tenant_id).all()
#         return campaigns
#     """
#     if not hasattr(request.state, "tenant_id"):
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="Tenant not identified"
#         )
#     return request.state.tenant_id
