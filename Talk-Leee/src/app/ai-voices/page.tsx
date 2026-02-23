"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { motion } from "framer-motion";
import { Play, Pause, Loader2 } from "lucide-react";
import { aiOptionsApi, type VoiceInfo } from "@/lib/ai-options-api";

type VoiceCard = {
  id: string;
  name: string;
  description: string;
  initial: string;
  language: string;
  provider: string;
  accentColor: string;
  previewText: string;
};

const accentStyles = ["#0EA5E9", "#10B981", "#F59E0B", "#EC4899", "#8B5CF6", "#22C55E"] as const;

function toVoiceCard(voice: VoiceInfo, index: number): VoiceCard {
  const accentColor = voice.accent_color || accentStyles[index % accentStyles.length] || "#10B981";
  return {
    id: voice.id,
    name: voice.name,
    description: voice.description,
    initial: (voice.name.trim().charAt(0) || "?").toUpperCase(),
    language: voice.language || "en",
    provider: voice.provider,
    accentColor,
    previewText: voice.preview_text || "Hello, I am your AI voice assistant. How can I help you today?",
  };
}

function decodeFloat32Base64(input: string): Float32Array {
  const binary = atob(input);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  const sampleCount = Math.floor(bytes.byteLength / 4);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const output = new Float32Array(sampleCount);
  for (let i = 0; i < sampleCount; i++) {
    output[i] = view.getFloat32(i * 4, true);
  }
  return output;
}

export default function AiVoicesPage() {
  const [voices, setVoices] = useState<VoiceCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const audioRef = useRef<{ ctx: AudioContext; source: AudioBufferSourceNode } | null>(null);

  const stopPreview = useCallback(async () => {
    const current = audioRef.current;
    if (!current) return;
    try {
      current.source.stop();
    } catch {
    }
    try {
      current.source.disconnect();
    } catch {
    }
    try {
      await current.ctx.close();
    } catch {
    }
    audioRef.current = null;
  }, []);

  useEffect(() => {
    const fetchVoices = async () => {
      try {
        setError(null);
        const data = await aiOptionsApi.getVoices();
        setVoices(data.map(toVoiceCard));
      } catch (err) {
        setError(err instanceof Error ? err.message : "An unknown error occurred");
      } finally {
        setLoading(false);
      }
    };

    void fetchVoices();
    return () => {
      void stopPreview();
    };
  }, [stopPreview]);

  const handlePreview = useCallback(
    async (voice: VoiceCard) => {
      if (playingId === voice.id) {
        await stopPreview();
        setPlayingId(null);
        return;
      }

      setError(null);
      setPlayingId(voice.id);

      try {
        await stopPreview();

        const preview = await aiOptionsApi.previewVoice({
          voice_id: voice.id,
          text: voice.previewText,
        });

        const samples = decodeFloat32Base64(preview.audio_base64);
        const sampleRate = 24000;
        const ctx = new AudioContext({ sampleRate });
        const buffer = ctx.createBuffer(1, samples.length, sampleRate);
        buffer.getChannelData(0).set(samples);

        const source = ctx.createBufferSource();
        source.buffer = buffer;
        source.connect(ctx.destination);
        source.onended = () => {
          setPlayingId((prev) => (prev === voice.id ? null : prev));
          void stopPreview();
        };

        audioRef.current = { ctx, source };
        source.start(0);
      } catch (err) {
        await stopPreview();
        setPlayingId(null);
        setError(err instanceof Error ? err.message : "Voice preview failed");
      }
    },
    [playingId, stopPreview]
  );

  return (
    <main className="min-h-screen bg-transparent home-navbar-offset">
      <Navbar />

      <div className="pt-10 md:pt-6 pb-24 px-4 md:px-6 lg:px-8 max-w-7xl mx-auto">
        <div className="text-center max-w-3xl mx-auto mb-16 space-y-4">
          <motion.h1
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="text-4xl md:text-5xl font-bold text-foreground tracking-tight"
          >
            AI Voices
          </motion.h1>
          <motion.p
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
            className="text-xl text-muted-foreground"
          >
            Choose from our library of natural, human-like AI voices
          </motion.p>
        </div>

        {loading ? (
          <div className="flex justify-center items-center py-20" role="status" aria-live="polite" aria-busy="true">
            <Loader2 className="w-12 h-12 text-indigo-600 animate-spin" aria-hidden />
            <span className="sr-only">Loading voices…</span>
          </div>
        ) : error ? (
          <div className="text-center py-20 text-red-600" role="alert" aria-live="assertive">
            <p>Error: {error}</p>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="mt-4 px-4 py-2 bg-indigo-600 text-white rounded-md hover:bg-indigo-700"
            >
              Retry
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
            {voices.map((voice, index) => (
              <motion.div
                key={voice.id}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: index * 0.1 }}
                className="group p-8 rounded-2xl border border-gray-200 bg-transparent shadow-sm transition-[transform,filter,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:brightness-[1.02] hover:border-gray-200 hover:shadow-md dark:border-border/70 flex flex-col items-center text-center"
                style={{
                  backgroundImage: "var(--home-card-gradient)",
                  backgroundSize: "cover",
                  backgroundRepeat: "no-repeat",
                }}
              >
                <div
                  className="w-20 h-20 rounded-full flex items-center justify-center mb-6 text-3xl font-bold group-hover:scale-110 transition-transform duration-300"
                  style={{
                    backgroundColor: `${voice.accentColor}26`,
                    color: voice.accentColor,
                  }}
                >
                  {voice.initial}
                </div>

                <h3 className="text-2xl font-bold text-foreground mb-2">{voice.name}</h3>

                <p className="text-muted-foreground mb-4 min-h-[3rem]">{voice.description}</p>
                <div className="mb-6 inline-flex items-center rounded-full border border-border bg-background/70 px-2.5 py-1 text-xs font-semibold text-muted-foreground">
                  {voice.provider} · {voice.language}
                </div>

                <button
                  type="button"
                  onClick={() => void handlePreview(voice)}
                  className="px-6 py-2.5 bg-indigo-600 text-white font-medium rounded-lg hover:bg-indigo-700 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 flex items-center gap-2"
                  aria-label={`Preview voice of ${voice.name}`}
                  aria-pressed={playingId === voice.id}
                >
                  {playingId === voice.id ? (
                    <>
                      <Pause className="w-4 h-4" aria-hidden /> Stop Preview
                    </>
                  ) : (
                    <>
                      <Play className="w-4 h-4" aria-hidden /> Preview Voice
                    </>
                  )}
                </button>
              </motion.div>
            ))}
          </div>
        )}
      </div>

      <Footer />
    </main>
  );
}
