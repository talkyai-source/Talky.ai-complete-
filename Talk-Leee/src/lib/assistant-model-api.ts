import { z } from "zod";
import { sharedHttpClient } from "@/lib/api";

export const AssistantModelSchema = z.object({ id: z.string(), name: z.string() });
export const AssistantModelStateSchema = z.object({
  current: z.string(),
  available: z.array(AssistantModelSchema).default([]),
});
export type AssistantModelState = z.infer<typeof AssistantModelStateSchema>;

// Optional widget: a 401 on these calls must NOT clear the token or fire the
// global session-expired redirect — it would log the user out just for opening
// the assistant. suppressAuthRedirect makes them fail soft (the picker hides).
export async function getAssistantModel(): Promise<AssistantModelState> {
  const raw = await sharedHttpClient().request({
    path: "/assistant/model",
    method: "GET",
    suppressAuthRedirect: true,
  });
  return AssistantModelStateSchema.parse(raw);
}

/**
 * Fetch a short-lived token to authenticate the assistant WebSocket.
 * The auth cookie reaches HTTP requests but NOT the cross-origin WS handshake,
 * so the WS would otherwise get no credential and close 1008 ("session expired").
 * This runs over normal authed HTTP (cookie works) and the caller sends the
 * token as the WS {type:"auth"} frame. Returns null if not authenticated
 * (fail soft — never tear down the session for the assistant).
 */
export async function getAssistantWsToken(): Promise<string | null> {
  try {
    const raw = await sharedHttpClient().request({
      path: "/assistant/ws-token",
      method: "GET",
      suppressAuthRedirect: true,
    });
    return z.object({ token: z.string() }).parse(raw).token;
  } catch {
    return null;
  }
}

export async function setAssistantModel(model: string): Promise<string> {
  const raw = await sharedHttpClient().request({
    path: "/assistant/model",
    method: "PUT",
    body: { model },
    suppressAuthRedirect: true,
  });
  return z.object({ current: z.string() }).parse(raw).current;
}
