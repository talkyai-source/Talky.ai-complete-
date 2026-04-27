"use client";

import { useAuth as useAuthContext } from "@/lib/auth-context";
import { UserRole, hasRole, hasAnyRole, getRolePermissions } from "@/lib/auth-roles";

export type { UserRole };

/**
 * Hook for accessing authentication state and role-based utilities.
 * Wraps the original AuthContext and adds role-checking helpers.
 */
export function useAuth() {
  const ctx = useAuthContext();

  const userRole = (ctx.user?.role as UserRole) || null;

  const canAccessRole = (requiredRole: UserRole): boolean => {
    return hasRole(userRole, requiredRole);
  };

  const hasAnyOfRoles = (roles: UserRole[]): boolean => {
    return hasAnyRole(userRole, roles);
  };

  const getPermissions = () => {
    return getRolePermissions(userRole);
  };

  return {
    ...ctx,
    isLoading: ctx.loading,
    error: null as string | null,
    token: null as string | null,
    isAuthenticated: !!ctx.user,
    hasMfaEnabled: false,
    hasPasskeyEnabled: false,
    canAccessRole,
    hasAnyOfRoles,
    getPermissions,
  };
}

/**
 * Hook specifically for checking if user can access admin features
 */
export function useAdminAccess() {
  const { user, canAccessRole } = useAuth();

  return {
    isAdmin: canAccessRole("tenant_admin"),
    isPlatformAdmin: user?.role === "platform_admin",
    isPartnerAdmin: user?.role === "partner_admin",
    isWhiteLabelAdmin: user?.role === "white_label_admin",
  };
}

/**
 * Hook for protecting routes based on role
 */
export function useAuthGuard(requiredRole?: UserRole) {
  const { user, isLoading, canAccessRole } = useAuth();

  const hasAccess = requiredRole ? canAccessRole(requiredRole) : !!user;

  return {
    hasAccess,
    isLoading,
    user,
  };
}
