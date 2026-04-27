import { NextResponse } from "next/server";
import { z } from "zod";
import { createHttpClient, isApiClientError } from "@/lib/http-client";
import { enforceMultiLevelRateLimit } from "@/server/api-security";

export type Voice = {
  id: string;
  name: string;
  description: string;
  initial: string;
  color: string;
  bg: string;
  previewUrl: string;
};

const VoiceApiItemSchema = z
  .object({
    id: z.string(),
    name: z.string(),
    description: z.string().optional(),
    preview_url: z.string().optional(),
    previewUrl: z.string().optional(),
  })
  .passthrough();

const VoiceApiResponseSchema = z.array(VoiceApiItemSchema);

const accentStyles = [
  { color: "text-indigo-600", bg: "bg-indigo-50" },
  { color: "text-emerald-600", bg: "bg-emerald-50" },
  { color: "text-purple-600", bg: "bg-purple-50" },
  { color: "text-amber-600", bg: "bg-amber-50" },
  { color: "text-slate-900", bg: "bg-white" },
  { color: "text-blue-600", bg: "bg-blue-50" },
] as const;

const FallbackVoices: Voice[] = [
  {
    id: "sarah",
    name: "Sarah",
    description: "Professional female voice",
    initial: "S",
    color: "text-indigo-600",
    bg: "bg-indigo-50",
    previewUrl: "",
  },
  {
    id: "michael",
    name: "Michael",
    description: "Confident male voice",
    initial: "M",
    color: "text-emerald-600",
    bg: "bg-emerald-50",
    previewUrl: "",
  },
  {
    id: "amelia",
    name: "Amelia",
    description: "Warm, friendly voice",
    initial: "A",
    color: "text-purple-600",
    bg: "bg-purple-50",
    previewUrl: "",
  },
  {
    id: "david",
    name: "David",
    description: "Clear, upbeat voice",
    initial: "D",
    color: "text-amber-600",
    bg: "bg-amber-50",
    previewUrl: "",
  },
  {
    id: "olivia",
    name: "Olivia",
    description: "Calm, modern voice",
    initial: "O",
    color: "text-blue-600",
    bg: "bg-blue-50",
    previewUrl: "",
  },
] as const;

function mapVoice(item: z.infer<typeof VoiceApiItemSchema>, index: number): Voice {
  const style = accentStyles[index % accentStyles.length];
  const initial = item.name.trim().slice(0, 1).toUpperCase() || "?";
  return {
    id: item.id,
    name: item.name,
    description: item.description ?? "",
    initial,
    color: style.color,
    bg: style.bg,
    previewUrl: item.previewUrl ?? item.preview_url ?? "",
  };
}

async function fetchVoices(baseUrl: string) {
  const client = createHttpClient({ baseUrl });
  try {
    const data = await client.request({ path: "/voices", timeoutMs: 12_000 });
    return VoiceApiResponseSchema.parse(data);
  } catch (err) {
    if (isApiClientError(err) && err.status === 404) {
      const data = await client.request({ path: "/ai/voices", timeoutMs: 12_000 });
      return VoiceApiResponseSchema.parse(data);
    }
    throw err;
  }
}

export async function GET(req: Request) {
  const rate = await enforceMultiLevelRateLimit({ request: req, tier: "default", path: "/api/voices", method: "GET" });
  if (!rate.ok) return NextResponse.json({ detail: "Too many requests" }, { status: 429, headers: rate.headers });

  const baseUrlRaw = process.env.NEXT_PUBLIC_API_BASE_URL;
  const baseUrlParsed = z.string().url().safeParse(baseUrlRaw);
  if (!baseUrlParsed.success) {
    return NextResponse.json(FallbackVoices, { headers: { "x-voices-source": "fallback", ...rate.headers } });
  }

  try {
    const voices = await fetchVoices(baseUrlParsed.data);
    return NextResponse.json(voices.map(mapVoice), { headers: rate.headers });
  } catch {
    return NextResponse.json(FallbackVoices, { headers: { "x-voices-source": "fallback", ...rate.headers } });
  }
}
