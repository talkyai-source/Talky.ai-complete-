"""
Database Seed Script: RBAC Roles and Permissions

This script populates the database with the default roles and permissions.
"""
import asyncio
import logging
from uuid import uuid4

from app.core.container import get_container
from app.core.security.rbac import UserRole, Permission, ROLE_DEFAULT_PERMISSIONS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def seed_rbac():
    container = get_container()
    await container.startup()
    
    db_pool = container.db_pool
    
    async with db_pool.acquire() as conn:
        logger.info("Seeding roles and permissions...")
        
        # 1. Seed Permissions
        perm_map = {}
        for p in Permission:
            resource, action = p.value.split(":")
            row = await conn.fetchrow(
                """
                INSERT INTO permissions (name, description, resource, action, is_system)
                VALUES ($1, $2, $3, $4, true)
                ON CONFLICT (resource, action) DO UPDATE SET name = EXCLUDED.name
                RETURNING id, name
                """,
                p.value, f"Permission to {action} {resource}", resource, action
            )
            perm_map[p.value] = row["id"]
        
        logger.info(f"Seeded {len(perm_map)} permissions.")
        
        # 2. Seed Roles
        role_map = {}
        role_levels = {
            UserRole.PLATFORM_ADMIN: 100,
            UserRole.PARTNER_ADMIN: 80,
            UserRole.TENANT_ADMIN: 60,
            UserRole.USER: 40,
            UserRole.READONLY: 20,
        }
        
        for role_enum, level in role_levels.items():
            is_system = True
            tenant_scoped = (role_enum != UserRole.PLATFORM_ADMIN)
            
            row = await conn.fetchrow(
                """
                INSERT INTO roles (name, description, level, is_system_role, tenant_scoped)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (name) DO UPDATE SET level = EXCLUDED.level
                RETURNING id, name
                """,
                role_enum.value, f"{role_enum.value.replace('_', ' ').title()} role", level, is_system, tenant_scoped
            )
            role_map[role_enum] = row["id"]
            
            # 3. Seed Role-Permission Mappings
            perms_for_role = ROLE_DEFAULT_PERMISSIONS.get(role_enum, set())
            for p in perms_for_role:
                await conn.execute(
                    """
                    INSERT INTO role_permissions (role_id, permission_id)
                    VALUES ($1, $2)
                    ON CONFLICT (role_id, permission_id) DO NOTHING
                    """,
                    role_map[role_enum], perm_map[p.value]
                )
        
        logger.info(f"Seeded {len(role_map)} roles and their permissions.")
        
    await container.shutdown()

if __name__ == "__main__":
    asyncio.run(seed_rbac())
