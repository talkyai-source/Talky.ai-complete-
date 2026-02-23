import type { StorybookConfig } from "@storybook/react-webpack5";
import path from "node:path";

const config: StorybookConfig = {
    core: {
        disableTelemetry: true,
    },
    framework: {
        name: "@storybook/react-webpack5",
        options: {},
    },
    stories: ["../src/**/*.stories.@(ts|tsx)"],
    addons: ["@storybook/addon-essentials"],
    webpackFinal: async (cfg) => {
        cfg.module = cfg.module ?? { rules: [] };
        cfg.module.rules = cfg.module.rules ?? [];

        cfg.resolve = cfg.resolve ?? {};
        cfg.resolve.extensions = Array.from(new Set([...(cfg.resolve.extensions ?? []), ".ts", ".tsx"]));
        cfg.resolve.alias = {
            ...(cfg.resolve.alias ?? {}),
            "@": path.resolve(__dirname, "../src"),
            "next/navigation": path.resolve(__dirname, "./mocks/next-navigation"),
            "next/link": path.resolve(__dirname, "./mocks/next-link"),
            "next/image": path.resolve(__dirname, "./mocks/next-image"),
        };
        cfg.resolve.fallback = {
            ...(cfg.resolve.fallback ?? {}),
            zlib: false,
        };
        return cfg;
    },
};

export default config;
