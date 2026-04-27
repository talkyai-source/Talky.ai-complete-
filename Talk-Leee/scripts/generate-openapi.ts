import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { zodToJsonSchema } from "zod-to-json-schema";
import {
    AssistantActionSchema,
    ConnectorAccountSchema,
    ConnectorSchema,
    ListResponseSchema,
    MeetingSchema,
    ReminderSchema,
} from "../src/lib/models";
import { backendEndpoints } from "../src/lib/backend-endpoints";
import { z } from "zod";

const argv = new Set(process.argv.slice(2));
const mode = argv.has("--check") ? "check" : "write";

function schemaFromZod(name: string, schema: z.ZodTypeAny) {
    const json = zodToJsonSchema(schema, { name, $refStrategy: "none" });
    const defs = (json as unknown as { definitions?: Record<string, unknown> }).definitions;
    const def = defs?.[name];
    if (def && typeof def === "object") return def as Record<string, unknown>;
    const { $schema, definitions, ...rest } = json as unknown as Record<string, unknown>;
    void $schema;
    void definitions;
    return rest;
}

function jsonStable(obj: unknown) {
    return JSON.stringify(obj, null, 2) + "\n";
}

const ConnectorListResponse = ListResponseSchema(ConnectorSchema);
const ConnectorAccountListResponse = ListResponseSchema(ConnectorAccountSchema);
const MeetingListResponse = ListResponseSchema(MeetingSchema);
const ReminderListResponse = ListResponseSchema(ReminderSchema);
const AssistantActionListResponse = ListResponseSchema(AssistantActionSchema);

const schemas = {
    Connector: schemaFromZod("Connector", ConnectorSchema),
    ConnectorAccount: schemaFromZod("ConnectorAccount", ConnectorAccountSchema),
    Meeting: schemaFromZod("Meeting", MeetingSchema),
    Reminder: schemaFromZod("Reminder", ReminderSchema),
    AssistantAction: schemaFromZod("AssistantAction", AssistantActionSchema),
    ConnectorListResponse: schemaFromZod("ConnectorListResponse", ConnectorListResponse),
    ConnectorAccountListResponse: schemaFromZod("ConnectorAccountListResponse", ConnectorAccountListResponse),
    MeetingListResponse: schemaFromZod("MeetingListResponse", MeetingListResponse),
    ReminderListResponse: schemaFromZod("ReminderListResponse", ReminderListResponse),
    AssistantActionListResponse: schemaFromZod("AssistantActionListResponse", AssistantActionListResponse),
    HealthResponse: {
        type: "object",
        additionalProperties: false,
        properties: { status: { type: "string", examples: ["ok"] } },
        required: ["status"],
    },
    ErrorResponse: {
        type: "object",
        additionalProperties: true,
        properties: { detail: { type: "string", examples: ["Unauthorized"] } },
        required: ["detail"],
    },
} as const;

const openapi = {
    openapi: "3.1.0",
    info: {
        title: "Talk-Lee API",
        version: "0.1.0",
        description: "Generated from frontend client contracts (schemas + endpoint registry).",
    },
    servers: [
        {
            url: process.env.NEXT_PUBLIC_API_BASE_URL || "/api/v1",
        },
    ],
    tags: [
        { name: "System" },
        { name: "Connectors" },
        { name: "Meetings" },
        { name: "Reminders" },
        { name: "Assistant" },
    ],
    components: {
        securitySchemes: {
            bearerAuth: { type: "http", scheme: "bearer", bearerFormat: "JWT" },
        },
        schemas,
    },
    security: [{ bearerAuth: [] }],
    paths: {
        [backendEndpoints.health.path]: {
            get: {
                tags: backendEndpoints.health.tags,
                summary: backendEndpoints.health.summary,
                security: [],
                responses: {
                    "200": {
                        description: "OK",
                        content: {
                            "application/json": {
                                schema: { $ref: "#/components/schemas/HealthResponse" },
                                examples: { ok: { value: { status: "ok" } } },
                            },
                        },
                    },
                },
            },
        },
        [backendEndpoints.connectorsList.path]: {
            get: {
                tags: backendEndpoints.connectorsList.tags,
                summary: backendEndpoints.connectorsList.summary,
                responses: {
                    "200": {
                        description: "OK",
                        content: { "application/json": { schema: { $ref: "#/components/schemas/ConnectorListResponse" } } },
                    },
                    "401": {
                        description: "Unauthorized",
                        content: { "application/json": { schema: { $ref: "#/components/schemas/ErrorResponse" } } },
                    },
                },
            },
            post: {
                tags: backendEndpoints.connectorsCreate.tags,
                summary: backendEndpoints.connectorsCreate.summary,
                requestBody: {
                    required: true,
                    content: {
                        "application/json": {
                            schema: {
                                type: "object",
                                additionalProperties: false,
                                properties: {
                                    name: { type: "string", examples: ["Google Calendar"] },
                                    type: { type: "string", examples: ["calendar"] },
                                    config: { type: "object", additionalProperties: true, examples: [{ clientId: "..." }] },
                                },
                                required: ["name", "type", "config"],
                            },
                            examples: {
                                create: { value: { name: "Google Calendar", type: "calendar", config: { clientId: "..." } } },
                            },
                        },
                    },
                },
                responses: {
                    "200": {
                        description: "Created",
                        content: { "application/json": { schema: { $ref: "#/components/schemas/Connector" } } },
                    },
                    "400": { description: "Bad request", content: { "application/json": { schema: { $ref: "#/components/schemas/ErrorResponse" } } } },
                    "401": { description: "Unauthorized", content: { "application/json": { schema: { $ref: "#/components/schemas/ErrorResponse" } } } },
                },
            },
        },
        [backendEndpoints.connectorAccountsList.path]: {
            get: {
                tags: backendEndpoints.connectorAccountsList.tags,
                summary: backendEndpoints.connectorAccountsList.summary,
                parameters: [
                    {
                        name: "connector_id",
                        in: "query",
                        required: false,
                        description: "Filter accounts by connector id.",
                        schema: { type: "string", examples: ["con-123"] },
                    },
                ],
                responses: {
                    "200": {
                        description: "OK",
                        content: { "application/json": { schema: { $ref: "#/components/schemas/ConnectorAccountListResponse" } } },
                    },
                    "401": { description: "Unauthorized", content: { "application/json": { schema: { $ref: "#/components/schemas/ErrorResponse" } } } },
                },
            },
        },
        [backendEndpoints.meetingsList.path]: {
            get: {
                tags: backendEndpoints.meetingsList.tags,
                summary: backendEndpoints.meetingsList.summary,
                responses: {
                    "200": {
                        description: "OK",
                        content: { "application/json": { schema: { $ref: "#/components/schemas/MeetingListResponse" } } },
                    },
                    "401": { description: "Unauthorized", content: { "application/json": { schema: { $ref: "#/components/schemas/ErrorResponse" } } } },
                },
            },
        },
        [backendEndpoints.remindersList.path]: {
            get: {
                tags: backendEndpoints.remindersList.tags,
                summary: backendEndpoints.remindersList.summary,
                responses: {
                    "200": {
                        description: "OK",
                        content: { "application/json": { schema: { $ref: "#/components/schemas/ReminderListResponse" } } },
                    },
                    "401": { description: "Unauthorized", content: { "application/json": { schema: { $ref: "#/components/schemas/ErrorResponse" } } } },
                },
            },
            post: {
                tags: backendEndpoints.remindersCreate.tags,
                summary: backendEndpoints.remindersCreate.summary,
                requestBody: {
                    required: true,
                    content: {
                        "application/json": {
                            schema: {
                                type: "object",
                                additionalProperties: false,
                                properties: {
                                    content: { type: "string" },
                                    channel: { type: "string", examples: ["email"] },
                                    scheduled_at: { type: "string", description: "ISO datetime", examples: ["2026-01-14T10:00:00Z"] },
                                    meeting_id: { type: "string" },
                                    meeting_title: { type: "string" },
                                    contact_id: { type: "string" },
                                    contact_name: { type: "string" },
                                    to_email: { type: "string" },
                                    to_phone: { type: "string" },
                                },
                                required: ["content", "channel", "scheduled_at"],
                            },
                            examples: {
                                email: { value: { content: "Reminder: meeting soon", channel: "email", scheduled_at: "2026-01-14T10:00:00Z", to_email: "name@example.com" } },
                            },
                        },
                    },
                },
                responses: {
                    "200": { description: "Created", content: { "application/json": { schema: { $ref: "#/components/schemas/Reminder" } } } },
                    "400": { description: "Bad request", content: { "application/json": { schema: { $ref: "#/components/schemas/ErrorResponse" } } } },
                    "401": { description: "Unauthorized", content: { "application/json": { schema: { $ref: "#/components/schemas/ErrorResponse" } } } },
                },
            },
        },
        [backendEndpoints.remindersUpdate.path]: {
            patch: {
                tags: backendEndpoints.remindersUpdate.tags,
                summary: backendEndpoints.remindersUpdate.summary,
                parameters: [
                    {
                        name: "id",
                        in: "path",
                        required: true,
                        description: "Reminder id.",
                        schema: { type: "string", examples: ["rem-123"] },
                    },
                ],
                requestBody: {
                    required: true,
                    content: {
                        "application/json": {
                            schema: {
                                type: "object",
                                additionalProperties: false,
                                properties: {
                                    content: { type: "string" },
                                    due_date: { type: "string", description: "ISO datetime", examples: ["2026-01-14T10:00:00Z"] },
                                    is_completed: { type: "boolean" },
                                },
                            },
                            examples: {
                                toggle: { value: { is_completed: true } },
                            },
                        },
                    },
                },
                responses: {
                    "200": { description: "OK", content: { "application/json": { schema: { $ref: "#/components/schemas/Reminder" } } } },
                    "401": { description: "Unauthorized", content: { "application/json": { schema: { $ref: "#/components/schemas/ErrorResponse" } } } },
                    "404": { description: "Not found", content: { "application/json": { schema: { $ref: "#/components/schemas/ErrorResponse" } } } },
                },
            },
        },
        [backendEndpoints.remindersCancel.path]: {
            post: {
                tags: backendEndpoints.remindersCancel.tags,
                summary: backendEndpoints.remindersCancel.summary,
                parameters: [
                    {
                        name: "id",
                        in: "path",
                        required: true,
                        description: "Reminder id.",
                        schema: { type: "string", examples: ["rem-123"] },
                    },
                ],
                responses: {
                    "200": { description: "OK", content: { "application/json": { schema: { $ref: "#/components/schemas/Reminder" } } } },
                    "401": { description: "Unauthorized", content: { "application/json": { schema: { $ref: "#/components/schemas/ErrorResponse" } } } },
                    "404": { description: "Not found", content: { "application/json": { schema: { $ref: "#/components/schemas/ErrorResponse" } } } },
                },
            },
        },
        [backendEndpoints.assistantActionsList.path]: {
            get: {
                tags: backendEndpoints.assistantActionsList.tags,
                summary: backendEndpoints.assistantActionsList.summary,
                responses: {
                    "200": {
                        description: "OK",
                        content: { "application/json": { schema: { $ref: "#/components/schemas/AssistantActionListResponse" } } },
                    },
                    "401": { description: "Unauthorized", content: { "application/json": { schema: { $ref: "#/components/schemas/ErrorResponse" } } } },
                },
            },
        },
    },
} as const;

const here = path.dirname(fileURLToPath(import.meta.url));
const outFile = path.resolve(here, "..", "public", "openapi.json");

const next = jsonStable(openapi);

if (mode === "check") {
    let current: string | undefined;
    try {
        current = readFileSync(outFile, "utf8");
    } catch {
        current = undefined;
    }
    if (!current || current !== next) {
        console.error("openapi.json is out of date. Run: npm run docs:openapi");
        process.exit(1);
    }
    process.exit(0);
}

writeFileSync(outFile, next, "utf8");
console.log(`Wrote ${outFile}`);
