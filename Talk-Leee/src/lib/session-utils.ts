/**
 * Session and Device Management Utilities
 * Handles active sessions, device listing, and logout operations
 *
 * Backend wiring (verified against deployed api.talkleeai.com):
 *   GET    /api/v1/sessions/active        → list (returns {sessions, total_count, current_session_id})
 *   DELETE /api/v1/sessions/{id}          → revoke one (cannot revoke current)
 *   POST   /api/v1/auth/logout            → log out current session
 *   POST   /api/v1/auth/logout-all        → log out every session including current
 *
 * Prior versions of this file hit /api/auth/* paths that don't exist on
 * this backend (returned 404), so the Devices tab never rendered.
 */

import { apiBaseUrl } from "@/lib/env";

export interface Device {
  id: string;
  name: string;
  type: "browser" | "mobile" | "desktop" | "unknown";
  os?: string;
  browser?: string;
  lastActivity: string;
  ipAddress?: string;
  isCurrent: boolean;
  createdAt: string;
}

export interface SessionInfo {
  id: string;
  userId: string;
  createdAt: string;
  lastActivity: string;
  expiresAt: string;
  isCurrent: boolean;
}

/**
 * Parses user agent to detect device and browser info
 */
export function parseUserAgent(userAgent: string): {
  browser: string;
  os: string;
  type: "browser" | "mobile" | "desktop" | "unknown";
} {
  const ua = userAgent.toLowerCase();

  // Detect OS
  let os = "Unknown";
  if (ua.includes("win")) os = "Windows";
  else if (ua.includes("mac")) os = "macOS";
  else if (ua.includes("linux")) os = "Linux";
  else if (ua.includes("iphone")) os = "iOS";
  else if (ua.includes("ipad")) os = "iPadOS";
  else if (ua.includes("android")) os = "Android";

  // Detect Browser
  let browser = "Unknown";
  if (ua.includes("chrome")) browser = "Chrome";
  else if (ua.includes("safari")) browser = "Safari";
  else if (ua.includes("firefox")) browser = "Firefox";
  else if (ua.includes("edge")) browser = "Edge";
  else if (ua.includes("opera")) browser = "Opera";

  // Detect Type
  let type: "browser" | "mobile" | "desktop" | "unknown" = "unknown";
  if (ua.includes("mobile") || ua.includes("android") || ua.includes("iphone")) {
    type = "mobile";
  } else if (ua.includes("windows") || ua.includes("mac") || ua.includes("linux")) {
    type = "desktop";
  } else {
    type = "browser";
  }

  return { browser, os, type };
}

/**
 * Generates a human-readable device name
 */
export function generateDeviceName(userAgent: string): string {
  const { browser, os, type } = parseUserAgent(userAgent);

  if (type === "mobile") {
    return `${browser} on ${os}`;
  }

  return `${browser} on ${os}`;
}

interface SessionInfoApi {
  id: string;
  device_name?: string | null;
  device_type?: string | null;
  browser?: string | null;
  os?: string | null;
  ip_address?: string | null;
  is_current: boolean;
  created_at: string;
  last_active_at: string;
  expires_at: string;
}

function authHeaders(token: string): HeadersInit {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function mapDevice(s: SessionInfoApi): Device {
  const fallbackType: Device["type"] = "unknown";
  const rawType = (s.device_type ?? "").toLowerCase();
  const type: Device["type"] =
    rawType === "mobile" || rawType === "desktop" || rawType === "browser"
      ? (rawType as Device["type"])
      : fallbackType;
  const name = s.device_name || [s.browser, s.os].filter(Boolean).join(" on ") || "Unknown device";
  return {
    id: s.id,
    name,
    type,
    os: s.os ?? undefined,
    browser: s.browser ?? undefined,
    lastActivity: s.last_active_at,
    ipAddress: s.ip_address ?? undefined,
    isCurrent: !!s.is_current,
    createdAt: s.created_at,
  };
}

/**
 * Gets list of all active devices/sessions
 */
export async function getActiveSessions(token: string): Promise<Device[]> {
  const base = apiBaseUrl();
  const response = await fetch(`${base}/sessions/active`, {
    method: "GET",
    credentials: "include",
    headers: authHeaders(token),
  });

  if (!response.ok) {
    throw new Error("Failed to fetch active sessions");
  }

  const payload = (await response.json()) as { sessions: SessionInfoApi[] };
  return (payload.sessions ?? []).map(mapDevice);
}

/**
 * Logs out a specific session/device. Backend refuses if you try to
 * revoke the current session — use logoutCurrentSession for that.
 */
export async function logoutSession(token: string, sessionId: string): Promise<{ success: boolean }> {
  const base = apiBaseUrl();
  const response = await fetch(`${base}/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
    credentials: "include",
    headers: authHeaders(token),
  });

  if (!response.ok) {
    let detail = "Failed to logout session";
    try {
      const j = await response.json();
      detail = j?.detail || j?.error?.message || detail;
    } catch {
      // ignore
    }
    throw new Error(detail);
  }

  return { success: true };
}

/**
 * Logs out all sessions except the current one.
 *
 * Backend has POST /auth/logout-all which logs out EVERY session including
 * current. To preserve the current session we list, then revoke each
 * non-current row.
 */
export async function logoutAllOtherSessions(token: string): Promise<{ success: boolean }> {
  const sessions = await getActiveSessions(token);
  const others = sessions.filter((s) => !s.isCurrent);
  // Fire revokes in parallel — backend will reject the current one anyway.
  await Promise.all(others.map((s) => logoutSession(token, s.id).catch(() => undefined)));
  return { success: true };
}

/**
 * Logs out the current session (user logout)
 */
export async function logoutCurrentSession(token: string): Promise<{ success: boolean }> {
  const base = apiBaseUrl();
  const response = await fetch(`${base}/auth/logout`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(token),
    },
  });

  if (!response.ok) {
    throw new Error("Failed to logout");
  }

  return { success: true };
}

/**
 * Formats timestamp to readable date string
 */
export function formatSessionTime(timestamp: string): string {
  const date = new Date(timestamp);
  const now = new Date();
  const diffInSeconds = Math.floor((now.getTime() - date.getTime()) / 1000);

  if (diffInSeconds < 60) return "Just now";
  if (diffInSeconds < 3600) return `${Math.floor(diffInSeconds / 60)} minutes ago`;
  if (diffInSeconds < 86400) return `${Math.floor(diffInSeconds / 3600)} hours ago`;
  if (diffInSeconds < 604800) return `${Math.floor(diffInSeconds / 86400)} days ago`;

  return date.toLocaleDateString();
}

/**
 * Gets a device icon based on type
 */
export function getDeviceIcon(type: string): string {
  switch (type) {
    case "mobile":
      return "📱";
    case "tablet":
      return "📱";
    case "desktop":
      return "💻";
    default:
      return "🖥️";
  }
}
