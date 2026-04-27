/**
 * Session and Device Management Utilities
 * Handles active sessions, device listing, and logout operations
 */

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

/**
 * Gets list of all active devices/sessions
 */
export async function getActiveSessions(token: string): Promise<Device[]> {
  const response = await fetch("/api/auth/sessions", {
    method: "GET",
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });

  if (!response.ok) {
    throw new Error("Failed to fetch active sessions");
  }

  return response.json();
}

/**
 * Logs out a specific session/device
 */
export async function logoutSession(token: string, sessionId: string): Promise<{ success: boolean }> {
  const response = await fetch(`/api/auth/sessions/${sessionId}/logout`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });

  if (!response.ok) {
    throw new Error("Failed to logout session");
  }

  return response.json();
}

/**
 * Logs out all other sessions (keeping current session active)
 */
export async function logoutAllOtherSessions(token: string): Promise<{ success: boolean }> {
  const response = await fetch("/api/auth/sessions/logout-all-others", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });

  if (!response.ok) {
    throw new Error("Failed to logout all other sessions");
  }

  return response.json();
}

/**
 * Logs out the current session (user logout)
 */
export async function logoutCurrentSession(token: string): Promise<{ success: boolean }> {
  const response = await fetch("/api/auth/logout", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
  });

  if (!response.ok) {
    throw new Error("Failed to logout");
  }

  return response.json();
}

/**
 * Renames a session/device
 */
export async function renameSession(token: string, sessionId: string, newName: string): Promise<{ success: boolean }> {
  const response = await fetch(`/api/auth/sessions/${sessionId}/rename`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ name: newName }),
  });

  if (!response.ok) {
    throw new Error("Failed to rename session");
  }

  return response.json();
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
