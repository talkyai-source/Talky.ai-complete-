"use client";

import { useState, useId } from "react";
import { LogOut, Loader2 } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { useRouter } from "next/navigation";
import { useAuth } from "@/hooks/useAuth";

interface LogoutButtonProps {
  token?: string;
  variant?: "default" | "destructive" | "outline" | "secondary" | "ghost" | "link";
  size?: "default" | "sm" | "lg" | "icon";
  showLabel?: boolean;
  onLogoutComplete?: () => void;
  onError?: (error: string) => void;
}

export default function LogoutButton({
  variant = "outline",
  size = "default",
  showLabel = true,
  onLogoutComplete,
  onError,
}: LogoutButtonProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const router = useRouter();
  const { logout } = useAuth();
  const queryClient = useQueryClient();
  const errorId = useId();

  async function handleLogout() {
    setLoading(true);
    setError("");

    try {
      const result = await logout();
      // A later login owns the browser now; this completion cannot clear it
      // or navigate away from it, even when the old revocation succeeded.
      if (!result.identityCurrent) return;

      // Security: wipe the React Query cache so the next person on a shared
      // device can't read the previous user's cached data (the cache is
      // in-memory and per-user — clearing it on logout is mandatory).
      queryClient.clear();

      if (result.serverConfirmed) {
        onLogoutComplete?.();
        router.push("/auth/login");
      } else {
        const message = "Signed out locally. Server session revocation was not confirmed.";
        setError(message);
        onError?.(message);
        router.push("/auth/login?logout=unconfirmed");
      }
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : "Logout failed";
      setError(errorMsg);
      onError?.(errorMsg);

      // An unexpected context error gives no identity ownership proof.
      // Keep the error visible rather than navigating a possibly newer login.
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
