/**
 * AI Options API Client
 * 
 * Handles communication with the AI Options backend endpoints
 * for provider selection, testing, and configuration.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

// Types
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

// Default configuration - Using Google TTS (Cartesia disabled)
export const DEFAULT_CONFIG: AIProviderConfig = {
    llm_provider: "groq",
    llm_model: "llama-3.3-70b-versatile",
    llm_temperature: 0.6,
    llm_max_tokens: 150,
    stt_provider: "deepgram",
    stt_model: "nova-3",
    stt_language: "en",
    tts_provider: "google",  // Changed from cartesia
    tts_model: "Chirp3-HD",
    tts_voice_id: "en-US-Chirp3-HD-Leda",  // Google Chirp3-HD Leda
    tts_sample_rate: 24000,  // Google Chirp3-HD sample rate
};

class AIOptionsApi {
    private getHeaders(): HeadersInit {
        const headers: HeadersInit = {
            "Content-Type": "application/json",
        };
        if (typeof window !== "undefined") {
            const token = localStorage.getItem("token");
            if (token) {
                (headers as Record<string, string>)["Authorization"] = `Bearer ${token}`;
            }
        }
        return headers;
    }

    private async request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
        const response = await fetch(`${API_BASE}${endpoint}`, {
            ...options,
            headers: this.getHeaders(),
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: "Request failed" }));
            throw new Error(error.detail);
        }

        return response.json();
    }

    // Get available providers and models
    async getProviders(): Promise<ProviderListResponse> {
        return this.request<ProviderListResponse>("/ai-options/providers");
    }

    // Get available TTS voices (curated list)
    async getVoices(): Promise<VoiceInfo[]> {
        return this.request<VoiceInfo[]>("/ai-options/voices");
    }

    // Preview a voice with sample audio
    async previewVoice(request: VoicePreviewRequest): Promise<VoicePreviewResponse> {
        return this.request<VoicePreviewResponse>("/ai-options/voices/preview", {
            method: "POST",
            body: JSON.stringify(request),
        });
    }

    // Get current configuration
    async getConfig(): Promise<AIProviderConfig> {
        return this.request<AIProviderConfig>("/ai-options/config");
    }

    // Save configuration
    async saveConfig(config: AIProviderConfig): Promise<AIProviderConfig> {
        return this.request<AIProviderConfig>("/ai-options/config", {
            method: "POST",
            body: JSON.stringify(config),
        });
    }

    // Test LLM with message
    async testLLM(request: LLMTestRequest): Promise<LLMTestResponse> {
        return this.request<LLMTestResponse>("/ai-options/test/llm", {
            method: "POST",
            body: JSON.stringify(request),
        });
    }

    // Test TTS with text
    async testTTS(request: TTSTestRequest): Promise<TTSTestResponse> {
        return this.request<TTSTestResponse>("/ai-options/test/tts", {
            method: "POST",
            body: JSON.stringify(request),
        });
    }

    // Run full benchmark
    async runBenchmark(config: AIProviderConfig): Promise<LatencyBenchmarkResponse> {
        return this.request<LatencyBenchmarkResponse>("/ai-options/benchmark", {
            method: "POST",
            body: JSON.stringify(config),
        });
    }
}

export const aiOptionsApi = new AIOptionsApi();
