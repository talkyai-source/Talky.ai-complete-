"""
Clients Endpoints
CRUD operations for client/contact management
"""
from app.core.error_handlers import safe_exception_message
from app.api.v1.validation import ResourceUUID, bounded_text
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import List, Optional
from uuid import UUID
from app.core.postgres_adapter import Client

from app.api.v1.dependencies import get_db_client, CurrentUser
from app.core.security.rbac import Permission, require_permission

router = APIRouter(prefix="/clients", tags=["clients"])


def _tenant_scoped(permission: Permission):
    """RBAC gate that also guarantees a tenant_id, so no query is ever unfiltered.

    Same contract as ``require_admin_tenant``: a tenant-less caller (only a
    platform_admin can be one) is refused rather than served every tenant's rows.
    """
    permission_gate = require_permission(permission)

    async def _gate(current_user: CurrentUser = Depends(permission_gate)) -> CurrentUser:
        if not current_user.tenant_id:
            raise HTTPException(status_code=403, detail="Tenant context required for this operation")
        return current_user

    return _gate


class ClientCreate(BaseModel):
    """Create client request (QA F2-13: name/company fit VARCHAR(255))."""
    default_country: Optional[str] = Field(None, min_length=2, max_length=2)
    name: bounded_text(255, min_length=1)
    company: Optional[bounded_text(255)] = None
    phone: Optional[bounded_text(32)] = None
    email: Optional[EmailStr] = None
    tags: List[bounded_text(64)] = Field(default_factory=list, max_length=50)
    notes: Optional[bounded_text(10_000)] = None


class ClientResponse(BaseModel):
    """Client response model"""
    id: str
    name: str
    company: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    tags: List[str] = []

    @field_validator("id", mode="before")
    @classmethod
    def _uuid_as_text(cls, value):
        # asyncpg returns uuid.UUID for the uuid column and pydantic v2 will not
        # coerce it to str, so every real row failed validation (QA F2-08).
        return str(value) if isinstance(value, UUID) else value

    @field_validator("tags", mode="before")
    @classmethod
    def _null_tags_are_empty(cls, value):
        # tags is a nullable jsonb column; NULL means no tags.
        return [] if value is None else value


@router.get("/", response_model=List[ClientResponse])
async def list_clients(
    current_user: CurrentUser = Depends(_tenant_scoped(Permission.CAMPAIGNS_READ)),
    db_client: Client = Depends(get_db_client)
):
    """
    Get all clients for the current tenant.
    
    Used by: /dashboard/clients page.
    """
    try:
        # Build query
        query = db_client.table("clients").select("id, name, company, phone, email, tags")
        query = query.eq("tenant_id", current_user.tenant_id)
        
        response = query.order("name").execute()
        
        clients = []
        for client in response.data or []:
            clients.append(ClientResponse(
                id=client["id"],
                name=client["name"],
                company=client.get("company"),
                phone=client.get("phone"),
                email=client.get("email"),
                tags=client.get("tags", [])
            ))
        
        return clients
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_exception_message(e, 'Failed to fetch clients.')
        )


@router.post("/", response_model=ClientResponse)
async def create_client(
    client: ClientCreate,
    current_user: CurrentUser = Depends(_tenant_scoped(Permission.CAMPAIGNS_UPDATE)),
    db_client: Client = Depends(get_db_client)
):
    """
    Create a new client.
    
    Used by: /dashboard/clients create form.
    """
    try:
        phone = client.phone
        if phone:
            from app.api.v1.endpoints.contacts import _resolve_country
            from app.domain.services.phone_number_normalizer import normalize_phone_number
            country = _resolve_country(db_client, current_user.tenant_id, requested=client.default_country)
            try:
                phone = normalize_phone_number(phone, country)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=[{"row": 1, "field": "phone",
                                    "message": "Invalid phone number for the selected country"}]) from exc
        # Prepare client data
        client_data = {
            "name": client.name,
            "company": client.company,
            "phone": phone,
            "email": client.email,
            "tags": client.tags,
            "notes": client.notes,
            "tenant_id": current_user.tenant_id
        }
        
        # Insert client
        response = db_client.table("clients").insert(client_data).execute()
        
        if not response.data:
            raise HTTPException(
                status_code=500,
                detail="Failed to create client"
            )
        
        created = response.data[0]
        
        return ClientResponse(
            id=created["id"],
            name=created["name"],
            company=created.get("company"),
            phone=created.get("phone"),
            email=created.get("email"),
            tags=created.get("tags", [])
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_exception_message(e, 'Failed to create client.')
        )


@router.get("/{client_id}", response_model=ClientResponse)
async def get_client(
    client_id: ResourceUUID,
    current_user: CurrentUser = Depends(_tenant_scoped(Permission.CAMPAIGNS_READ)),
    db_client: Client = Depends(get_db_client)
):
    """
    Get a single client by ID.
    """
    try:
        query = db_client.table("clients").select("*").eq("id", client_id)
        
        query = query.eq("tenant_id", current_user.tenant_id)
        
        response = query.single().execute()
        
        if not response.data:
            raise HTTPException(
                status_code=404,
                detail="Client not found"
            )
        
        client = response.data
        
        return ClientResponse(
            id=client["id"],
            name=client["name"],
            company=client.get("company"),
            phone=client.get("phone"),
            email=client.get("email"),
            tags=client.get("tags", [])
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_exception_message(e, 'Failed to fetch client.')
        )


@router.delete("/{client_id}")
async def delete_client(
    client_id: ResourceUUID,
    current_user: CurrentUser = Depends(_tenant_scoped(Permission.CAMPAIGNS_UPDATE)),
    db_client: Client = Depends(get_db_client)
):
    """
    Delete a client by ID.
    """
    try:
        query = db_client.table("clients").delete().eq("id", client_id)
        
        query = query.eq("tenant_id", current_user.tenant_id)
        
        response = query.execute()
        
        if not response.data:
            raise HTTPException(
                status_code=404,
                detail="Client not found"
            )
        
        return {"detail": "Client deleted"}
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_exception_message(e, 'Failed to delete client.')
        )
