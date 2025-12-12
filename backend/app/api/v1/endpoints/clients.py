"""
Clients Endpoints
CRUD operations for client/contact management
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from typing import List, Optional
from supabase import Client

from app.api.v1.dependencies import get_supabase, get_current_user, CurrentUser

router = APIRouter(prefix="/clients", tags=["clients"])


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
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Get all clients for the current tenant.
    
    Used by: /dashboard/clients page.
    """
    try:
        # Build query
        query = supabase.table("clients").select("id, name, company, phone, email, tags")
        
        # Filter by tenant if user has one
        if current_user.tenant_id:
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
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
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
        response = supabase.table("clients").insert(client_data).execute()
        
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
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Get a single client by ID.
    """
    try:
        query = supabase.table("clients").select("*").eq("id", client_id)
        
        # Filter by tenant if user has one
        if current_user.tenant_id:
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
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Delete a client by ID.
    """
    try:
        query = supabase.table("clients").delete().eq("id", client_id)
        
        # Filter by tenant if user has one
        if current_user.tenant_id:
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
