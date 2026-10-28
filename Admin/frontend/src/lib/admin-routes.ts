/** Navigation mirrors existing backend require_admin / require_platform_admin gates. */
export const ADMIN_ROUTES = [
    { id: 'command-center', label: 'Command Center', path: '/', platformOnly: true },
    { id: 'tenants', label: 'Tenants', path: '/tenants', platformOnly: true },
    { id: 'enquiries', label: 'Website Enquiries', path: '/enquiries', platformOnly: true },
    { id: 'users', label: 'Users & Roles', path: '/users', platformOnly: true },
    { id: 'calls', label: 'Calls', path: '/calls', platformOnly: true },
    { id: 'inbound', label: 'Inbound Control', path: '/inbound', platformOnly: true },
    { id: 'actions', label: 'Actions', path: '/actions', platformOnly: false },
    { id: 'connectors', label: 'Connectors', path: '/connectors', platformOnly: true },
    { id: 'usage-cost', label: 'Usage & Cost', path: '/usage-cost', platformOnly: true },
    { id: 'incidents', label: 'Incidents', path: '/incidents', platformOnly: true },
    { id: 'system-health', label: 'System Health', path: '/system-health', platformOnly: true },
] as const;
export function normalizedAdminRole(role: string | undefined): string {
    if (role === 'super_admin') return 'platform_admin';
    if (role === 'admin' || role === 'owner') return 'tenant_admin';
    return role ?? '';
}
export function canAccessAdminRoute(path: string, role: string | undefined): boolean {
    const rule = ADMIN_ROUTES.find((item) => item.path === (path === '/command-center' ? '/' : path));
    const current = normalizedAdminRole(role);
    return Boolean(rule && (current === 'platform_admin' || (!rule.platformOnly && ['partner_admin', 'tenant_admin'].includes(current))));
}
export function adminLanding(role: string | undefined): string {
    return normalizedAdminRole(role) === 'platform_admin' ? '/' : '/actions';
}
export function safeAdminReturn(path: unknown, role: string | undefined): string {
    return typeof path === 'string' && canAccessAdminRoute(path, role) ? path : adminLanding(role);
}
