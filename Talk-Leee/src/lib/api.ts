import { z } from "zod";
import { setBrowserAuthToken } from "@/lib/auth-token";
import { createHttpClient, ApiClientError } from "@/lib/http-client";
import { apiBaseUrl } from "@/lib/env";

/* ------------------------------------------------------------------ */
/*  Response Schemas                                                   */
/* ------------------------------------------------------------------ */

export const LoginResponseSchema = z
    .object({
        access_token: z.string(),
        token_type: z.string().optional(),
        user_id: z.string(),
        email: z.string().email(),
        role: z.string(),
        business_name: z.string().optional().nullable(),
        minutes_remaining: z.number().optional(),
        message: z.string().optional(),
    })
    .passthrough()
    .transform((v) => ({
        access_token: v.access_token,
        token_type: v.token_type ?? "bearer",
        user_id: v.user_id,
        email: v.email,
        role: v.role,
        business_name: v.business_name ?? undefined,
        minutes_remaining: v.minutes_remaining ?? 0,
        message: v.message ?? "",
    }));

export type LoginResponse = z.infer<typeof LoginResponseSchema>;

export const RegisterResponseSchema = LoginResponseSchema;
export type RegisterResponse = LoginResponse;

export const MeResponseSchema = z
    .object({
        id: z.string(),
        email: z.string().email(),
        name: z.string().optional().nullable(),
        business_name: z.string().optional().nullable(),
        role: z.string(),
        minutes_remaining: z.number(),
    })
    .passthrough()
    .transform((v) => ({
        ...v,
        name: v.name ?? undefined,
        business_name: v.business_name ?? undefined,
    }));

export type MeResponse = z.infer<typeof MeResponseSchema>;

export const ChangePasswordResponseSchema = z
    .object({
        detail: z.string(),
    })
    .passthrough();

export type ChangePasswordResponse = z.infer<typeof ChangePasswordResponseSchema>;

/* ------------------------------------------------------------------ */
/*  API Client                                                         */
/* ------------------------------------------------------------------ */

class ApiClient {
    private _client: ReturnType<typeof createHttpClient> | undefined;

    private client() {
        if (this._client) return this._client;
        this._client = createHttpClient({ baseUrl: apiBaseUrl() });
        return this._client;
    }

    private parseOrThrow<T>(
        schema: { safeParse: (v: unknown) => { success: true; data: T } | { success: false } },
        data: unknown,
        meta: { url: string; method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" },
    ) {
        const parsed = schema.safeParse(data);
        if (parsed.success) return parsed.data;
        throw new ApiClientError({
            code: "invalid_response",
            message: "Invalid response format",
            url: meta.url,
            method: meta.method,
            details: data,
        });
    }

    setToken(token: string) {
        setBrowserAuthToken(token);
    }

    clearToken() {
        setBrowserAuthToken(null);
    }

    /* ---------- Auth ---------- */

    async login(email: string, password: string): Promise<LoginResponse> {
        const path = "/auth/login";
        const method = "POST" as const;
        const data = await this.client().request({
            path,
            method,
            body: { email, password },
            timeoutMs: 12_000,
        });
        return this.parseOrThrow(LoginResponseSchema, data, { url: `${apiBaseUrl()}${path}`, method });
    }

    async register(
        email: string,
        password: string,
        businessName: string,
        planId: string = "basic",
        name?: string,
    ): Promise<RegisterResponse> {
        const path = "/auth/register";
        const method = "POST" as const;
        const data = await this.client().request({
            path,
            method,
            body: {
                email,
                password,
                business_name: businessName,
                plan_id: planId,
                ...(name ? { name } : {}),
            },
            timeoutMs: 12_000,
        });
        return this.parseOrThrow(RegisterResponseSchema, data, { url: `${apiBaseUrl()}${path}`, method });
    }

    async getMe(): Promise<MeResponse> {
        const method = "GET" as const;
        try {
            const path = "/auth/me";
            const data = await this.client().request({ path, method, timeoutMs: 12_000 });
            return this.parseOrThrow(MeResponseSchema, data, { url: `${apiBaseUrl()}${path}`, method });
        } catch (err) {
            if (err instanceof ApiClientError && err.status === 404) {
                const path = "/me";
                const data = await this.client().request({ path, method, timeoutMs: 12_000 });
                return this.parseOrThrow(MeResponseSchema, data, { url: `${apiBaseUrl()}${path}`, method });
            }
            throw err;
        }
    }

    async updateMe(input: { name?: string; business_name?: string }): Promise<MeResponse> {
        const path = "/auth/me";
        const method = "PATCH" as const;
        const data = await this.client().request({
            path,
            method,
            body: input,
            timeoutMs: 12_000,
        });
        return this.parseOrThrow(MeResponseSchema, data, { url: `${apiBaseUrl()}${path}`, method });
    }

    async changePassword(oldPassword: string, newPassword: string): Promise<ChangePasswordResponse> {
        const path = "/auth/change-password";
        const method = "POST" as const;
        const data = await this.client().request({
            path,
            method,
            body: {
                old_password: oldPassword,
                new_password: newPassword,
            },
            timeoutMs: 12_000,
        });
        return this.parseOrThrow(ChangePasswordResponseSchema, data, { url: `${apiBaseUrl()}${path}`, method });
    }

    async logout(): Promise<void> {
        try {
            await this.client().request({ path: "/auth/logout", method: "POST", timeoutMs: 12_000 });
        } catch (err) {
            if (err instanceof ApiClientError && (err.status === 404 || err.status === 405)) {
                // Ignore
            } else {
                throw err;
            }
        } finally {
            this.clearToken();
        }
    }

    async health(): Promise<{ status: string }> {
        const path = "/health";
        return this.client().request({ path, method: "GET", timeoutMs: 2500 });
    }
}

export const api = new ApiClient();
