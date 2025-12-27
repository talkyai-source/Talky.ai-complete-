"""
Tenant Filter Utility
Shared helper for applying consistent tenant filtering across Supabase queries
"""
from typing import Optional, Any


def apply_tenant_filter(query: Any, tenant_id: Optional[str], column: str = "tenant_id") -> Any:
    """
    Apply tenant filtering to a Supabase query.
    
    Centralizes tenant filtering logic to prevent copy-paste errors and ensure
    consistent handling of edge cases across all endpoints.
    
    Args:
        query: Supabase query builder object (from supabase.table(...).select(...))
        tenant_id: Current user's tenant_id (may be None for admin users)
        column: Name of the tenant_id column (default: "tenant_id")
    
    Returns:
        Modified query with tenant filter applied, or original query if tenant_id is None
    
    Usage:
        query = supabase.table("calls").select("*")
        query = apply_tenant_filter(query, current_user.tenant_id)
        response = query.execute()
    
    Note:
        When tenant_id is None (for admin users or users without a tenant),
        no filtering is applied. This allows admin endpoints to work across
        all tenants while regular users are restricted to their own data.
    """
    if tenant_id:
        return query.eq(column, tenant_id)
    return query


def verify_tenant_access(
    supabase: Any,
    table: str,
    record_id: str,
    tenant_id: Optional[str],
    tenant_column: str = "tenant_id"
) -> bool:
    """
    Verify that a record belongs to the specified tenant.
    
    Use this before performing operations on individual records to ensure
    the user has access to that specific record.
    
    Args:
        supabase: Supabase client instance
        table: Table name to check
        record_id: UUID of the record to verify
        tenant_id: Current user's tenant_id
        tenant_column: Name of the tenant_id column
    
    Returns:
        True if record exists and belongs to tenant, False otherwise
    
    Usage:
        if not verify_tenant_access(supabase, "calls", call_id, current_user.tenant_id):
            raise HTTPException(status_code=404, detail="Call not found")
    """
    if not tenant_id:
        # Admin users can access any record
        return True
    
    try:
        response = supabase.table(table).select("id").eq("id", record_id).eq(tenant_column, tenant_id).execute()
        return bool(response.data)
    except Exception:
        return False
