import { NextResponse } from "next/server";
import { z } from "zod";
import { createHttpClient, isApiClientError } from "@/lib/http-client";

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
    preview_url: z.string().nullish(),
    previewUrl: z.string().nullish(),
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
  const data = await client.request({ path: "/ai-options/voices", timeoutMs: 12_000 });
  return VoiceApiResponseSchema.parse(data);
}

export async function GET() {
  const baseUrlRaw = process.env.NEXT_PUBLIC_API_BASE_URL;
  const baseUrlParsed = z.string().url().safeParse(baseUrlRaw);

  if (!baseUrlParsed.success) {
    return NextResponse.json(
      { error: "NEXT_PUBLIC_API_BASE_URL is missing or invalid." },
      { status: 500 }
    );
  }

  try {
    const voices = await fetchVoices(baseUrlParsed.data);
    return NextResponse.json(voices.map(mapVoice));
  } catch (err) {
    if (isApiClientError(err)) {
      return NextResponse.json(
        { error: err.message || "Failed to load voices." },
        { status: err.status ?? 502 }
      );
    }

    return NextResponse.json(
      { error: "Failed to load voices." },
      { status: 502 }
    );
  }
}
