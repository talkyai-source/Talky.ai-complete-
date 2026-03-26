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

    /* ---------- Passkeys (WebAuthn) ---------- */

    async checkUserHasPasskeys(email: string): Promise<boolean> {
        const path = "/auth/passkey-check";
        const method = "POST" as const;
        try {
            const data = await this.client().request({
                path,
                method,
                body: { email },
                timeoutMs: 5_000,
            });
            return z.object({ has_passkeys: z.boolean() }).parse(data).has_passkeys;
        } catch {
            return false;
        }
    }

    async beginPasskeyLogin(email?: string): Promise<{
        ceremony_id: string;
        options: Record<string, unknown>;
        has_passkeys: boolean;
    }> {
        const path = "/auth/passkeys/login/begin";
        const method = "POST" as const;
        const data = await this.client().request({
            path,
            method,
            body: email ? { email } : {},
            timeoutMs: 10_000,
        });
        return z.object({
            ceremony_id: z.string(),
            options: z.record(z.unknown()),
            has_passkeys: z.boolean(),
        }).parse(data);
    }

    async completePasskeyLogin(
        ceremonyId: string,
        credentialResponse: Record<string, unknown>
    ): Promise<LoginResponse> {
        const path = "/auth/passkeys/login/complete";
        const method = "POST" as const;
        const data = await this.client().request({
            path,
            method,
            body: {
                ceremony_id: ceremonyId,
                credential_response: credentialResponse,
            },
            timeoutMs: 10_000,
        });
        return this.parseOrThrow(LoginResponseSchema, data, { url: `${apiBaseUrl()}${path}`, method });
    }

    async beginPasskeyRegistration(
        authenticatorType: "platform" | "cross-platform" | "any" = "any",
        displayName?: string
    ): Promise<{ ceremony_id: string; options: Record<string, unknown> }> {
        const path = "/auth/passkeys/register/begin";
        const method = "POST" as const;
        const data = await this.client().request({
            path,
            method,
            body: {
                authenticator_type: authenticatorType,
                display_name: displayName,
            },
            timeoutMs: 10_000,
        });
        return z.object({
            ceremony_id: z.string(),
            options: z.record(z.unknown()),
        }).parse(data);
    }

    async completePasskeyRegistration(
        ceremonyId: string,
        credentialResponse: Record<string, unknown>,
        displayName?: string
    ): Promise<{ passkey_id: string; message: string }> {
        const path = "/auth/passkeys/register/complete";
        const method = "POST" as const;
        const data = await this.client().request({
            path,
            method,
            body: {
                ceremony_id: ceremonyId,
                credential_response: credentialResponse,
                display_name: displayName,
            },
            timeoutMs: 10_000,
        });
        return z.object({
            passkey_id: z.string(),
            message: z.string(),
        }).parse(data);
    }

    async listPasskeys(): Promise<Array<{
        id: string;
        credential_id: string;
        display_name: string;
        device_type: string;
        backed_up: boolean;
        transports: string[];
        created_at: string;
        last_used_at?: string;
    }>> {
        const path = "/auth/passkeys";
        const method = "GET" as const;
        const data = await this.client().request({ path, method, timeoutMs: 10_000 });
        return z.object({
            passkeys: z.array(z.object({
                id: z.string(),
                credential_id: z.string(),
                display_name: z.string(),
                device_type: z.string(),
                backed_up: z.boolean(),
                transports: z.array(z.string()),
                created_at: z.string(),
                last_used_at: z.string().optional(),
            })),
        }).parse(data).passkeys;
    }

    async updatePasskey(passkeyId: string, displayName: string): Promise<void> {
        const path = `/auth/passkeys/${passkeyId}`;
        const method = "PATCH" as const;
        await this.client().request({
            path,
            method,
            body: { display_name: displayName },
            timeoutMs: 10_000,
        });
    }

    async deletePasskey(passkeyId: string): Promise<void> {
        const path = `/auth/passkeys/${passkeyId}`;
        const method = "DELETE" as const;
        await this.client().request({ path, method, timeoutMs: 10_000 });
    }
}

export const api = new ApiClient();
