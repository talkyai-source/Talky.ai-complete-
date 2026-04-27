"use client";

import { ReactNode } from "react";
import { UserRole, hasRole, hasAnyRole } from "@/lib/auth-roles";

interface RoleBasedRenderProps {
  role: UserRole | null;
  requiredRole?: UserRole;
  requiredRoles?: UserRole[];
  fallback?: ReactNode;
  children: ReactNode;
}

/**
 * Component for conditional rendering based on user role
 * Shows children only if user has required role
 */
export function RoleBasedRender({
  role,
  requiredRole,
  requiredRoles,
  children,
}: RoleBasedRenderProps) {
  let hasAccess = false;

  if (requiredRole) {
    hasAccess = hasRole(role, requiredRole);
  } else if (requiredRoles && requiredRoles.length > 0) {
    hasAccess = hasAnyRole(role, requiredRoles);
  } else {
    // If no requirement specified, show for authenticated users
    hasAccess = !!role;
  }

  return hasAccess ? <>{children}</> : null;
}

interface AdminOnlyProps {
  fallback?: ReactNode;
  children: ReactNode;
}

/**
 * Component for rendering content only for admin users
 */
export function AdminOnly({ children }: AdminOnlyProps) {
  return <>{children}</>;
}

interface PlatformAdminOnlyProps {
  children: ReactNode;
}

/**
 * Component for rendering content only for platform admins
 */
export function PlatformAdminOnly({ children }: PlatformAdminOnlyProps) {
  return <>{children}</>;
}
