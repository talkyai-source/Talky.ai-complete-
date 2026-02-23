"""
API v1 Request/Response Schemas

Pydantic models for request validation and response serialisation.
Place endpoint-specific schemas here instead of inlining them.

Example:
    # schemas/campaign.py
    class CampaignCreate(BaseModel):
        name: str
        description: Optional[str] = None

    class CampaignResponse(BaseModel):
        id: str
        name: str
        status: str
"""
