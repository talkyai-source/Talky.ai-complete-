import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";

export function createTestQueryClient() {
    return new QueryClient({
        defaultOptions: {
            queries: { retry: false, gcTime: Infinity },
            mutations: { retry: false, gcTime: Infinity },
        },
    });
}

export function renderWithQueryClient(ui: React.ReactElement) {
    const qc = createTestQueryClient();

    return {
        qc,
        ...render(React.createElement(QueryClientProvider, { client: qc }, ui)),
    };
}
