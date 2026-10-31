"""
Clients Endpoints
CRUD operations for client/contact management
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from typing import List, Optional
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
    """Create client request"""
    name: str
    company: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    tags: List[str] = []
    notes: Optional[str] = None


class ClientResponse(BaseModel):
    """Client response model"""
    id: str
    name: str
    company: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    tags: List[str] = []


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
            detail=f"Failed to fetch clients: {str(e)}"
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
        # Prepare client data
        client_data = {
            "name": client.name,
            "company": client.company,
            "phone": client.phone,
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
            detail=f"Failed to create client: {str(e)}"
        )


@router.get("/{client_id}", response_model=ClientResponse)
async def get_client(
    client_id: str,
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
            detail=f"Failed to fetch client: {str(e)}"
        )


@router.delete("/{client_id}")
async def delete_client(
    client_id: str,
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
            detail=f"Failed to delete client: {str(e)}"
        )
