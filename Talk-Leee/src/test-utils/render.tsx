import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";

export function renderWithQueryClient(ui: React.ReactElement) {
    const qc = new QueryClient({
        defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
        },
    });

    return {
        qc,
        ...render(React.createElement(QueryClientProvider, { client: qc }, ui)),
    };
}
