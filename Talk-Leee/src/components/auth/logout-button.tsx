"use client";

import { useState, useId } from "react";
import { LogOut, Loader2 } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { useRouter } from "next/navigation";
import { logoutCurrentSession } from "@/lib/session-utils";

interface LogoutButtonProps {
  token: string;
  variant?: "default" | "destructive" | "outline" | "secondary" | "ghost" | "link";
  size?: "default" | "sm" | "lg" | "icon";
  showLabel?: boolean;
  onLogoutComplete?: () => void;
  onError?: (error: string) => void;
}

export default function LogoutButton({
  token,
  variant = "outline",
  size = "default",
  showLabel = true,
  onLogoutComplete,
  onError,
}: LogoutButtonProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const router = useRouter();
  const queryClient = useQueryClient();
  const errorId = useId();

  async function handleLogout() {
    setLoading(true);
    setError("");

    try {
      // Call logout API — backend clears talky_at + talky_rt cookies and
      // AuthContext.logout (Phase 2) drops the canonical localStorage
      // key. The Phase 7 universal-auth-state cleanup removed the
      // `access_token` / `refresh_token` localStorage scrub here: those
      // keys were never properly written, so scrubbing them is dead
      // ceremony that misled readers into thinking the keys were live.
      await logoutCurrentSession(token);

      // Security: wipe the React Query cache so the next person on a shared
      // device can't read the previous user's cached data (the cache is
      // in-memory and per-user — clearing it on logout is mandatory).
      queryClient.clear();

      // Call success callback if provided
      onLogoutComplete?.();

      // Redirect to login
      router.push("/auth/login");
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : "Logout failed";
      setError(errorMsg);
      onError?.(errorMsg);

      setTimeout(() => {
        router.push("/auth/login");
      }, 1500);
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <Button
        type="button"
        variant={variant}
        size={size}
        onClick={handleLogout}
        disabled={loading}
        aria-describedby={error ? errorId : undefined}
      >
        {loading ? (
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        ) : (
          <LogOut className="h-4 w-4" aria-hidden />
        )}
        {showLabel && (
          <span className="ml-2">
            {loading ? "Signing out..." : "Sign out"}
          </span>
        )}
      </Button>

      {error && (
        <div
          id={errorId}
          role="alert"
          aria-live="assertive"
          className="text-xs text-red-600 dark:text-red-400 mt-1"
        >
          {error}
        </div>
      )}
    </>
  );
}
