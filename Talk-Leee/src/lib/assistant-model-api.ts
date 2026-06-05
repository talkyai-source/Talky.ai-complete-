import { z } from "zod";
import { sharedHttpClient } from "@/lib/api";

export const AssistantModelSchema = z.object({ id: z.string(), name: z.string() });
export const AssistantModelStateSchema = z.object({
  current: z.string(),
  available: z.array(AssistantModelSchema).default([]),
});
export type AssistantModelState = z.infer<typeof AssistantModelStateSchema>;

export async function getAssistantModel(): Promise<AssistantModelState> {
  const raw = await sharedHttpClient().request({ path: "/assistant/model", method: "GET" });
  return AssistantModelStateSchema.parse(raw);
}

export async function setAssistantModel(model: string): Promise<string> {
  const raw = await sharedHttpClient().request({
    path: "/assistant/model",
    method: "PUT",
    body: { model },
  });
  return z.object({ current: z.string() }).parse(raw).current;
}
