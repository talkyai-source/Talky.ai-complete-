/**
 * Authentication Context and Role-Based Utilities
 * Helps with role-based conditional UI rendering throughout the app
 */

export type UserRole =
  | "platform_admin"
  | "partner_admin"
  | "tenant_admin"
  | "user"
  | "readonly"
  | "white_label_admin";

export interface User {
  id: string;
  email: string;
  name: string;
  role: UserRole;
  tenantId?: string;
  partnerId?: string;
  mfaEnabled: boolean;
  passkeyEnabled: boolean;
}

export interface AuthContext {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
}

/**
 * Role hierarchy for permission checks
 * Higher numbers = more permissions
 */
export const roleHierarchy: Record<UserRole, number> = {
  "readonly": 1,
  "user": 2,
  "tenant_admin": 3,
  "partner_admin": 4,
  "white_label_admin": 4,
  "platform_admin": 5,
};

/**
 * Checks if user has required role or higher
 */
export function hasRole(userRole: UserRole | null, requiredRole: UserRole): boolean {
  if (!userRole) return false;
  return (roleHierarchy[userRole] || 0) >= (roleHierarchy[requiredRole] || 0);
}

/**
 * Checks if user has any of the specified roles
 */
export function hasAnyRole(userRole: UserRole | null, roles: UserRole[]): boolean {
  if (!userRole) return false;
  return roles.includes(userRole);
}

/**
 * Gets user-friendly role label
 */
export function getRoleLabel(role: UserRole): string {
  const labels: Record<UserRole, string> = {
    "readonly": "Read-Only Access",
    "user": "Standard User",
    "tenant_admin": "Tenant Administrator",
    "partner_admin": "Partner Administrator",
    "white_label_admin": "White Label Administrator",
    "platform_admin": "Platform Administrator",
  };
  return labels[role] || "Unknown Role";
}

/**
 * Gets role-specific features/permissions
 */
export function getRolePermissions(role: UserRole | null): {
  canManageUsers: boolean;
  canManageBilling: boolean;
  canViewAnalytics: boolean;
  canManageSettings: boolean;
  canAccessAdmin: boolean;
  canCreateTenants: boolean;
  canManagePartners: boolean;
} {
  if (!role) {
    return {
      canManageUsers: false,
      canManageBilling: false,
      canViewAnalytics: false,
      canManageSettings: false,
      canAccessAdmin: false,
      canCreateTenants: false,
      canManagePartners: false,
    };
  }

  const basePermissions = {
    canManageUsers: false,
    canManageBilling: false,
    canViewAnalytics: false,
    canManageSettings: true, // All authenticated users
    canAccessAdmin: false,
    canCreateTenants: false,
    canManagePartners: false,
  };

  switch (role) {
    case "readonly":
      return basePermissions;

    case "user":
      return {
        ...basePermissions,
        canViewAnalytics: true,
      };

    case "tenant_admin":
      return {
        ...basePermissions,
        canManageUsers: true,
        canManageBilling: true,
        canViewAnalytics: true,
        canAccessAdmin: true,
      };

    case "partner_admin":
      return {
        ...basePermissions,
        canManageUsers: true,
        canManageBilling: true,
        canViewAnalytics: true,
        canAccessAdmin: true,
        canCreateTenants: true,
        canManagePartners: false,
      };

    case "white_label_admin":
      return {
        ...basePermissions,
        canManageUsers: true,
        canManageBilling: true,
        canViewAnalytics: true,
        canAccessAdmin: true,
        canCreateTenants: true,
        canManagePartners: false,
      };

    case "platform_admin":
      return {
        ...basePermissions,
        canManageUsers: true,
        canManageBilling: true,
        canViewAnalytics: true,
        canAccessAdmin: true,
        canCreateTenants: true,
        canManagePartners: true,
      };

    default:
      return basePermissions;
  }
}

/**
 * Auth states for UI rendering
 */
export const AuthStates = {
  UNAUTHENTICATED: "unauthenticated",
  LOADING: "loading",
  AUTHENTICATED: "authenticated",
  ERROR: "error",
} as const;
