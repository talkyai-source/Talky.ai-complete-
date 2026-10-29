import { isApiClientError } from "./http-client";

/** Display only the API's UUID correlation reference, never its raw details. */
export function withSupportReference(error: unknown, message: string): string {
    const reference = isApiClientError(error) ? error.requestId : undefined;
    return typeof reference === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(reference)
        ? `${message} Support reference: ${reference}.`
        : message;
}
