import { z } from "zod";
import { createHttpClient } from "@/lib/http-client";
import { apiBaseUrl } from "@/lib/env";

export interface ModelInfo {
    id: string;
    name: string;
    description: string;
    speed?: string;
    price?: string;
    context_window?: number;
    is_preview?: boolean;
}

export interface VoiceInfo {
    id: string;
    name: string;
    language: string;
    description: string;
    gender?: string;
    accent?: string;
    accent_color: string;
    preview_text: string;
    provider: string;
    tags: string[];
}

export interface ProviderListResponse {
    llm: {
        providers: string[];
        models: ModelInfo[];
    };
    stt: {
        providers: string[];
        models: ModelInfo[];
    };
    tts: {
        providers: string[];
        models: ModelInfo[];
    };
}

export interface AIProviderConfig {
    llm_provider: string;
    llm_model: string;
    llm_temperature: number;
    llm_max_tokens: number;
    stt_provider: string;
    stt_model: string;
    stt_language: string;
    tts_provider: string;
    tts_model: string;
    tts_voice_id: string;
    tts_sample_rate: number;
}

export interface LLMTestRequest {
    model: string;
    message: string;
    temperature?: number;
    max_tokens?: number;
}

export interface LLMTestResponse {
    response: string;
    latency_ms: number;
    first_token_ms: number;
    total_tokens: number;
    model: string;
}

export interface TTSTestRequest {
    model: string;
    voice_id: string;
    text: string;
    sample_rate?: number;
}

export interface TTSTestResponse {
    audio_base64: string;
    latency_ms: number;
    first_audio_ms: number;
    duration_seconds: number;
    model: string;
    voice_id: string;
}

export interface VoicePreviewRequest {
    voice_id: string;
    text?: string;
}

export interface VoicePreviewResponse {
    voice_id: string;
    voice_name: string;
    audio_base64: string;
    duration_seconds: number;
    latency_ms: number;
}

export interface LatencyBenchmarkResponse {
    llm_first_token_ms: number;
    llm_total_ms: number;
    tts_first_audio_ms: number;
    tts_total_ms: number;
    total_pipeline_ms: number;
}

export const DEFAULT_CONFIG: AIProviderConfig = {
    llm_provider: "groq",
    llm_model: "llama-3.3-70b-versatile",
    llm_temperature: 0.6,
    llm_max_tokens: 150,
    stt_provider: "deepgram",
    stt_model: "nova-3",
    stt_language: "en",
    tts_provider: "cartesia",
    tts_model: "sonic-3",
    tts_voice_id: "f786b574-daa5-4673-aa0c-cbe3e8534c02",
    tts_sample_rate: 16000,
};

const RawModelSchema = z
    .object({
        id: z.string(),
        name: z.string(),
        description: z.string(),
        speed: z.string().optional(),
        price: z.string().optional(),
        context_window: z.number().optional(),
        contextWindow: z.number().optional(),
        is_preview: z.boolean().optional(),
        isPreview: z.boolean().optional(),
    })
    .passthrough();

const RawVoiceSchema = z
    .object({
        id: z.string(),
        name: z.string(),
        language: z.string().optional(),
        description: z.string(),
        gender: z.string().optional(),
        accent: z.string().optional(),
        accent_color: z.string().optional(),
        accentColor: z.string().optional(),
        preview_text: z.string().optional(),
        previewText: z.string().optional(),
        provider: z.string(),
        tags: z.array(z.string()).optional(),
    })
    .passthrough();

const RawProvidersBucketSchema = z
    .object({
        providers: z.array(z.string()),
        models: z.array(RawModelSchema),
    })
    .passthrough();

const RawProviderListSchema = z
    .object({
        llm: RawProvidersBucketSchema,
        stt: RawProvidersBucketSchema,
        tts: RawProvidersBucketSchema,
    })
    .passthrough();

const RawConfigSchema = z
    .object({
        llm_provider: z.string().optional(),
        llmProvider: z.string().optional(),
        llm_model: z.string().optional(),
        llmModel: z.string().optional(),
        llm_temperature: z.number().optional(),
        llmTemperature: z.number().optional(),
        llm_max_tokens: z.number().optional(),
        llmMaxTokens: z.number().optional(),
        stt_provider: z.string().optional(),
        sttProvider: z.string().optional(),
        stt_model: z.string().optional(),
        sttModel: z.string().optional(),
        stt_language: z.string().optional(),
        sttLanguage: z.string().optional(),
        tts_provider: z.string().optional(),
        ttsProvider: z.string().optional(),
        tts_model: z.string().optional(),
        ttsModel: z.string().optional(),
        tts_voice_id: z.string().optional(),
        ttsVoiceId: z.string().optional(),
        tts_sample_rate: z.number().optional(),
        ttsSampleRate: z.number().optional(),
    })
    .passthrough();

const RawVoicePreviewResponseSchema = z
    .object({
        voice_id: z.string().optional(),
        voiceId: z.string().optional(),
        voice_name: z.string().optional(),
        voiceName: z.string().optional(),
        audio_base64: z.string(),
        audioBase64: z.string().optional(),
        duration_seconds: z.number().optional(),
        durationSeconds: z.number().optional(),
        latency_ms: z.number().optional(),
        latencyMs: z.number().optional(),
    })
    .passthrough();

const RawLLMTestResponseSchema = z
    .object({
        response: z.string(),
        latency_ms: z.number().optional(),
        latencyMs: z.number().optional(),
        first_token_ms: z.number().optional(),
        firstTokenMs: z.number().optional(),
        total_tokens: z.number().optional(),
        totalTokens: z.number().optional(),
        model: z.string(),
    })
    .passthrough();

const RawTTSTestResponseSchema = z
    .object({
        audio_base64: z.string(),
        audioBase64: z.string().optional(),
        latency_ms: z.number().optional(),
        latencyMs: z.number().optional(),
        first_audio_ms: z.number().optional(),
        firstAudioMs: z.number().optional(),
        duration_seconds: z.number().optional(),
        durationSeconds: z.number().optional(),
        model: z.string(),
        voice_id: z.string().optional(),
        voiceId: z.string().optional(),
    })
    .passthrough();

const RawLatencyBenchmarkResponseSchema = z
    .object({
        llm_first_token_ms: z.number().optional(),
        llmFirstTokenMs: z.number().optional(),
        llm_total_ms: z.number().optional(),
        llmTotalMs: z.number().optional(),
        tts_first_audio_ms: z.number().optional(),
        ttsFirstAudioMs: z.number().optional(),
        tts_total_ms: z.number().optional(),
        ttsTotalMs: z.number().optional(),
        total_pipeline_ms: z.number().optional(),
        totalPipelineMs: z.number().optional(),
    })
    .passthrough();

let _httpClient: ReturnType<typeof createHttpClient> | undefined;

function httpClient() {
    if (_httpClient) return _httpClient;
    _httpClient = createHttpClient({ baseUrl: apiBaseUrl() });
    return _httpClient;
}

function normalizeModel(model: z.infer<typeof RawModelSchema>): ModelInfo {
    return {
        id: model.id,
        name: model.name,
        description: model.description,
        speed: model.speed,
        price: model.price,
        context_window: model.context_window ?? model.contextWindow,
        is_preview: model.is_preview ?? model.isPreview,
    };
}

function normalizeVoice(voice: z.infer<typeof RawVoiceSchema>): VoiceInfo {
    return {
        id: voice.id,
        name: voice.name,
        language: voice.language ?? "Unknown",
        description: voice.description,
        gender: voice.gender,
        accent: voice.accent,
        accent_color: voice.accent_color ?? voice.accentColor ?? "#64748B",
        preview_text: voice.preview_text ?? voice.previewText ?? "",
        provider: voice.provider,
        tags: voice.tags ?? [],
    };
}

function normalizeProviderList(raw: z.infer<typeof RawProviderListSchema>): ProviderListResponse {
    return {
        llm: { providers: raw.llm.providers, models: raw.llm.models.map(normalizeModel) },
        stt: { providers: raw.stt.providers, models: raw.stt.models.map(normalizeModel) },
        tts: { providers: raw.tts.providers, models: raw.tts.models.map(normalizeModel) },
    };
}

function pickNonEmptyString(...values: Array<string | undefined>) {
    for (const value of values) {
        if (typeof value === "string" && value.trim().length > 0) return value;
    }
    return undefined;
}

function normalizeConfig(raw: z.infer<typeof RawConfigSchema>): AIProviderConfig {
    return {
        llm_provider: pickNonEmptyString(raw.llm_provider, raw.llmProvider) ?? DEFAULT_CONFIG.llm_provider,
        llm_model: pickNonEmptyString(raw.llm_model, raw.llmModel) ?? DEFAULT_CONFIG.llm_model,
        llm_temperature: raw.llm_temperature ?? raw.llmTemperature ?? DEFAULT_CONFIG.llm_temperature,
        llm_max_tokens: raw.llm_max_tokens ?? raw.llmMaxTokens ?? DEFAULT_CONFIG.llm_max_tokens,
        stt_provider: pickNonEmptyString(raw.stt_provider, raw.sttProvider) ?? DEFAULT_CONFIG.stt_provider,
        stt_model: pickNonEmptyString(raw.stt_model, raw.sttModel) ?? DEFAULT_CONFIG.stt_model,
        stt_language: pickNonEmptyString(raw.stt_language, raw.sttLanguage) ?? DEFAULT_CONFIG.stt_language,
        tts_provider: pickNonEmptyString(raw.tts_provider, raw.ttsProvider) ?? DEFAULT_CONFIG.tts_provider,
        tts_model: pickNonEmptyString(raw.tts_model, raw.ttsModel) ?? DEFAULT_CONFIG.tts_model,
        tts_voice_id: pickNonEmptyString(raw.tts_voice_id, raw.ttsVoiceId) ?? DEFAULT_CONFIG.tts_voice_id,
        tts_sample_rate: raw.tts_sample_rate ?? raw.ttsSampleRate ?? DEFAULT_CONFIG.tts_sample_rate,
    };
}

async function requestFirstMatch<T>(paths: string[], opts: { method?: "GET" | "POST"; body?: unknown; timeoutMs?: number }) {
    let lastErr: unknown;
    for (const path of paths) {
        try {
            const data = await httpClient().request({ path, method: opts.method, body: opts.body, timeoutMs: opts.timeoutMs ?? 12_000 });
            return data as T;
        } catch (err) {
            lastErr = err;
        }
    }
    throw lastErr;
}

class AIOptionsApi {
    // Get available providers and models
    async getProviders(): Promise<ProviderListResponse> {
        const data = await requestFirstMatch<unknown>(["/ai/options/providers", "/ai/providers"], { timeoutMs: 12_000 });
        return normalizeProviderList(RawProviderListSchema.parse(data));
    }

    // Get available TTS voices
    async getVoices(): Promise<VoiceInfo[]> {
        const data = await requestFirstMatch<unknown>(["/ai/options/voices", "/ai/voices", "/voices"], { timeoutMs: 12_000 });
        return z.array(RawVoiceSchema).parse(data).map(normalizeVoice);
    }

    // Preview a voice with sample audio
    async previewVoice(request: VoicePreviewRequest): Promise<VoicePreviewResponse> {
        const data = await requestFirstMatch<unknown>(["/ai/options/voices/preview", "/ai/voices/preview", "/ai/voice/preview"], {
            method: "POST",
            body: request,
            timeoutMs: 30_000,
        });
        const parsed = RawVoicePreviewResponseSchema.parse(data);
        return {
            voice_id: parsed.voice_id ?? parsed.voiceId ?? request.voice_id,
            voice_name: parsed.voice_name ?? parsed.voiceName ?? "",
            audio_base64: parsed.audio_base64 ?? parsed.audioBase64 ?? "",
            duration_seconds: parsed.duration_seconds ?? parsed.durationSeconds ?? 0,
            latency_ms: parsed.latency_ms ?? parsed.latencyMs ?? 0,
        };
    }

    // Get current configuration
    async getConfig(): Promise<AIProviderConfig> {
        const data = await requestFirstMatch<unknown>(["/ai/options/config", "/ai/config"], { timeoutMs: 12_000 });
        return normalizeConfig(RawConfigSchema.parse(data));
    }

    // Save configuration
    async saveConfig(config: AIProviderConfig): Promise<AIProviderConfig> {
        const data = await requestFirstMatch<unknown>(["/ai/options/config", "/ai/config"], { method: "POST", body: config, timeoutMs: 12_000 });
        return normalizeConfig(RawConfigSchema.parse(data));
    }

    // Test LLM with message
    async testLLM(request: LLMTestRequest): Promise<LLMTestResponse> {
        const data = await requestFirstMatch<unknown>(["/ai/options/test/llm", "/ai/test/llm", "/ai/llm/test"], { method: "POST", body: request, timeoutMs: 30_000 });
        const parsed = RawLLMTestResponseSchema.parse(data);
        return {
            response: parsed.response,
            latency_ms: parsed.latency_ms ?? parsed.latencyMs ?? 0,
            first_token_ms: parsed.first_token_ms ?? parsed.firstTokenMs ?? 0,
            total_tokens: parsed.total_tokens ?? parsed.totalTokens ?? 0,
            model: parsed.model,
        };
    }

    // Test TTS with text
    async testTTS(request: TTSTestRequest): Promise<TTSTestResponse> {
        const data = await requestFirstMatch<unknown>(["/ai/options/test/tts", "/ai/test/tts", "/ai/tts/test"], { method: "POST", body: request, timeoutMs: 30_000 });
        const parsed = RawTTSTestResponseSchema.parse(data);
        return {
            audio_base64: parsed.audio_base64 ?? parsed.audioBase64 ?? "",
            latency_ms: parsed.latency_ms ?? parsed.latencyMs ?? 0,
            first_audio_ms: parsed.first_audio_ms ?? parsed.firstAudioMs ?? 0,
            duration_seconds: parsed.duration_seconds ?? parsed.durationSeconds ?? 0,
            model: parsed.model,
            voice_id: parsed.voice_id ?? parsed.voiceId ?? request.voice_id,
        };
    }

    // Run full benchmark
    async runBenchmark(_config: AIProviderConfig): Promise<LatencyBenchmarkResponse> {
        const data = await requestFirstMatch<unknown>(["/ai/options/benchmark", "/ai/benchmark"], { method: "POST", body: _config, timeoutMs: 60_000 });
        const parsed = RawLatencyBenchmarkResponseSchema.parse(data);
        return {
            llm_first_token_ms: parsed.llm_first_token_ms ?? parsed.llmFirstTokenMs ?? 0,
            llm_total_ms: parsed.llm_total_ms ?? parsed.llmTotalMs ?? 0,
            tts_first_audio_ms: parsed.tts_first_audio_ms ?? parsed.ttsFirstAudioMs ?? 0,
            tts_total_ms: parsed.tts_total_ms ?? parsed.ttsTotalMs ?? 0,
            total_pipeline_ms: parsed.total_pipeline_ms ?? parsed.totalPipelineMs ?? 0,
        };
    }
}

export const aiOptionsApi = new AIOptionsApi();
