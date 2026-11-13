"use client";

import { useCallback, useEffect, useId, useState } from "react";
import { Loader2, LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorState, LoadingState, failedReadMessage } from "@/components/states/page-states";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Device, getActiveSessions, logoutSession, logoutAllOtherSessions, formatSessionTime, getDeviceIcon } from "@/lib/session-utils";

interface DeviceListProps {
  token: string;
  onError?: (error: string) => void;
}

export default function DeviceList({ token, onError }: DeviceListProps) {
  const [devices, setDevices] = useState<Device[]>([]);
  const [loading, setLoading] = useState(true);
  const [loggingOutId, setLoggingOutId] = useState<string | null>(null);
  const [error, setError] = useState("");
  // A failed read is its own state: the list is unknown, not empty.
  const [loadError, setLoadError] = useState("");
  // The "Sign out from all other devices" dialog is open.
  const [confirmAllOpen, setConfirmAllOpen] = useState(false);
  const errorId = useId();

  // Fetch active sessions
  const fetchDevices = useCallback(async () => {
    try {
      setLoading(true);
      const sessions = await getActiveSessions(token);
      setDevices(sessions);
      setError("");
      setLoadError("");
    } catch (err) {
      const errorMsg = failedReadMessage(err, "Failed to fetch devices");
      setLoadError(errorMsg);
      onError?.(errorMsg);
    } finally {
      setLoading(false);
    }
  }, [token, onError]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- mount data fetch; fetchDevices sets loading/devices/error as it resolves
    void fetchDevices();
  }, [fetchDevices]);

  async function handleLogoutDevice(deviceId: string) {
    setLoggingOutId(deviceId);

    try {
      await logoutSession(token, deviceId);
      setDevices((prev) => prev.filter((d) => d.id !== deviceId));
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : "Failed to logout device";
      setError(errorMsg);
    } finally {
      setLoggingOutId(null);
    }
  }

  // Runs from the dialog's confirm button. A failure is shown in the card's
  // own error line, so the dialog always closes.
  async function handleLogoutAllOthers() {
    setLoggingOutId("all");

    try {
      await logoutAllOtherSessions(token);
      // Keep only current device
      setDevices((prev) => prev.filter((d) => d.isCurrent));
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : "Failed to logout other sessions";
      setError(errorMsg);
    } finally {
      setLoggingOutId(null);
    }
  }

  if (loading) {
    return <LoadingState />;
  }

  if (loadError) {
    return <ErrorState message={loadError} onRetry={fetchDevices} />;
  }

  return (
    <Card className="dark:border-white/10 dark:bg-white/5">
      <CardHeader>
        <CardTitle className="dark:text-white">Active Devices</CardTitle>
        <CardDescription>Manage your active sessions and devices</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && (
          <div
            id={errorId}
            role="alert"
            aria-live="assertive"
            className="text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 rounded-md p-3"
          >
            {error}
          </div>
        )}

        {devices.length === 0 ? (
          <EmptyState title="No active devices" />
        ) : (
          <>
            <div className="space-y-2">
              {devices.map((device) => (
                <div
                  key={device.id}
                  className="flex items-center justify-between rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm transition-[transform,background-color,box-shadow,border-color] duration-150 ease-out hover:-translate-y-0.5 hover:bg-gray-50 hover:shadow-md dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)]"
                >
                  <div className="flex items-center gap-3 flex-1 min-w-0">
                    <span className="text-xl" aria-hidden>
                      {getDeviceIcon(device.type)}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-medium text-gray-900 dark:text-zinc-100 truncate">
                          {device.name}
                        </p>
                        {device.isCurrent && (
                          <span className="inline-block px-2 py-0.5 bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 text-xs font-medium rounded">
                            Current
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        <span>Last active: {formatSessionTime(device.lastActivity)}</span>
                        {device.ipAddress && <span>• {device.ipAddress}</span>}
                      </div>
                    </div>
                  </div>

                  {!device.isCurrent && (
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={() => handleLogoutDevice(device.id)}
                      disabled={loggingOutId === device.id}
                      className="text-red-600 hover:text-red-700 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/30"
                    >
                      {loggingOutId === device.id ? (
                        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                      ) : (
                        <LogOut className="h-4 w-4" aria-hidden />
                      )}
                      <span className="sr-only">Logout from {device.name}</span>
                    </Button>
                  )}
                </div>
              ))}
            </div>

            {devices.length > 1 && (
              <div className="pt-4 border-t border-border">
                <Button
                  type="button"
                  variant="destructive"
                  className="w-full"
                  onClick={() => setConfirmAllOpen(true)}
                  disabled={loggingOutId === "all"}
                >
                  {loggingOutId === "all" ? (
                    <Loader2 className="h-4 w-4 animate-spin mr-2" aria-hidden />
                  ) : (
                    <LogOut className="h-4 w-4 mr-2" aria-hidden />
                  )}
                  Sign out from all other devices
                </Button>
              </div>
            )}
          </>
        )}
      </CardContent>
      <ConfirmDialog
        open={confirmAllOpen}
        onOpenChange={setConfirmAllOpen}
        intent="cancel"
        title="Sign out from all other devices"
        description=""
        warningText="Are you sure you want to sign out from all other devices?"
        confirmLabel="Sign out from all other devices"
        onConfirm={handleLogoutAllOthers}
      />
    </Card>
  );
}
