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

export async function setAssistantModel(model: string): Promise<string> {
  const raw = await sharedHttpClient().request({
    path: "/assistant/model",
    method: "PUT",
    body: { model },
    suppressAuthRedirect: true,
  });
  return z.object({ current: z.string() }).parse(raw).current;
}
