import postgres from "postgres";

type SqlClient = ReturnType<typeof postgres>;

function globalSqlStore() {
    return globalThis as unknown as { __talkleeSql?: SqlClient };
}

export function databaseUrl(): string | null {
    const raw = process.env.DATABASE_URL;
    if (!raw) return null;
    const v = String(raw).trim();
    if (!v) return null;
    return v;
}

export function isDatabaseConfigured() {
    return databaseUrl() !== null;
}

export function getSql(): SqlClient {
    const g = globalSqlStore();
    if (g.__talkleeSql) return g.__talkleeSql;

    const url = databaseUrl();
    if (!url) {
        throw new Error("DATABASE_URL is required");
    }

    const isProd = process.env.NODE_ENV === "production";

    g.__talkleeSql = postgres(url, {
        max: isProd ? 20 : 5,
        idle_timeout: 20,
        connect_timeout: 10,
        ssl: isProd ? "require" : undefined,
    });

    return g.__talkleeSql;
}

