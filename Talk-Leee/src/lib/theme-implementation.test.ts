import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const srcDir = path.join(__dirname, "..");

test("Theme Implementation Verification", async (t) => {
    await t.test("globals.css defines theme variables and transitions", () => {
        const globalsCssPath = path.join(srcDir, "app", "globals.css");
        const contents = readFileSync(globalsCssPath, "utf8");

        // Check for dark mode selector
        assert.match(contents, /\.dark \{/, "Should contain .dark class definition");
        
        // Check for specific colors requested
        assert.match(contents, /--background: #121212;/, "Should define dark background as #121212");
        assert.match(contents, /--background: #FFFFFF;/, "Should define light background as #FFFFFF");
        
        // Check for transitions
        assert.match(contents, /transition: background-color 300ms ease-in-out/, "Should have 300ms background transition");
    });

    await t.test("theme-provider.tsx implements localStorage and context", () => {
        const providerPath = path.join(srcDir, "components", "providers", "theme-provider.tsx");
        assert.ok(existsSync(providerPath), "theme-provider.tsx should exist");
        
        const contents = readFileSync(providerPath, "utf8");
        
        // Check for localStorage usage
        assert.match(contents, /localStorage\.getItem/, "Should get theme from localStorage");
        assert.match(contents, /localStorage\.setItem/, "Should save theme to localStorage");
        
        // Check for system preference detection
        assert.match(contents, /window\.matchMedia/, "Should check system preference");
        
        // Check for context
        assert.match(contents, /createContext/, "Should use createContext");
    });

    await t.test("navbar.tsx includes theme toggle", () => {
        const navbarPath = path.join(srcDir, "components", "home", "navbar.tsx");
        const contents = readFileSync(navbarPath, "utf8");
        
        assert.match(contents, /useTheme/, "Should import and use useTheme");
        assert.match(contents, /toggleTheme/, "Should use toggleTheme function");
        // Check for the button
        assert.match(contents, /button/, "Should contain a button");
        assert.match(contents, /onClick=\{toggleTheme\}/, "Button should call toggleTheme");
    });

    await t.test("sidebar.tsx inherits theme", () => {
        const sidebarPath = path.join(srcDir, "components", "layout", "sidebar.tsx");
        const contents = readFileSync(sidebarPath, "utf8");
        
        assert.match(contents, /useTheme/, "Should import useTheme");
        // Should not receive theme as prop anymore
        assert.doesNotMatch(contents, /props:.*theme/, "Should not accept theme prop");
    });
});
