"use client";

import { useState, useId } from "react";
import { LogOut, Loader2 } from "lucide-react";
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
  const errorId = useId();

  async function handleLogout() {
    setLoading(true);
    setError("");

    try {
      // Call logout API
      await logoutCurrentSession(token);

      // Clear local storage
      localStorage.removeItem("access_token");
      localStorage.removeItem("refresh_token");

      // Call success callback if provided
      onLogoutComplete?.();

      // Redirect to login
      router.push("/auth/login");
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : "Logout failed";
      setError(errorMsg);
      onError?.(errorMsg);

      // Still clear tokens and redirect on error
      localStorage.removeItem("access_token");
      localStorage.removeItem("refresh_token");
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
