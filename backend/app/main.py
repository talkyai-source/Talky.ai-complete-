"""
FastAPI Application Entry Point
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.routes import api_router

app = FastAPI(
    title="AI Voice Dialer",
    description="Intelligent voice communication platform with AI agents",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# MULTI-TENANT: Uncomment the lines below to enable multi-tenancy
# from app.core.tenant_middleware import TenantMiddleware
# app.add_middleware(TenantMiddleware)
# See MULTI_TENANT_ACTIVATION_GUIDE.md for complete activation instructions

# Include API routes
app.include_router(api_router, prefix="/api/v1")

@app.get("/")
async def root():
    return {"message": "AI Voice Dialer API", "status": "running"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

