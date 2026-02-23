"use client";

import { createContext, useContext, useEffect, useState } from "react";

type Theme = "dark" | "light";

interface ThemeProviderProps {
    children: React.ReactNode;
    defaultTheme?: Theme;
    storageKey?: string;
}

interface ThemeProviderState {
    theme: Theme;
    setTheme: (theme: Theme) => void;
    toggleTheme: () => void;
}

const initialState: ThemeProviderState = {
    theme: "light",
    setTheme: () => null,
    toggleTheme: () => null,
};

const ThemeProviderContext = createContext<ThemeProviderState>(initialState);

export function ThemeProvider({
    children,
    defaultTheme = "light",
    storageKey = "talklee.theme",
}: ThemeProviderProps) {
    const [theme, setThemeState] = useState<Theme>(defaultTheme);
    const [mounted, setMounted] = useState(false);

    useEffect(() => {
        setMounted(true);
        const savedTheme = localStorage.getItem(storageKey) as Theme;
        if (savedTheme) {
            setThemeState(savedTheme);
        } else if (window.matchMedia?.("(prefers-color-scheme: dark)")?.matches) {
            setThemeState("dark");
        }
    }, [storageKey]);

    useEffect(() => {
        if (!mounted) return;
        
        const root = window.document.documentElement;
        root.classList.remove("light", "dark");
        root.classList.add(theme);
        localStorage.setItem(storageKey, theme);
    }, [theme, storageKey, mounted]);

    const setTheme = (theme: Theme) => {
        setThemeState(theme);
    };

    const toggleTheme = () => {
        setThemeState((prev) => (prev === "dark" ? "light" : "dark"));
    };

    const value = {
        theme,
        setTheme,
        toggleTheme,
    };

    // Prevent hydration mismatch by not rendering until mounted, 
    // or rendering a placeholder/loading state if strict consistency is needed.
    // However, for themes, it's often better to render children to avoid layout shift,
    // accepting a potential flash of wrong theme if SSR differs.
    // To avoid flash, we can use script injection in head (not doing here for simplicity unless requested).
    
    return (
        <ThemeProviderContext.Provider value={value}>
            {children}
        </ThemeProviderContext.Provider>
    );
}

export const useTheme = () => {
    const context = useContext(ThemeProviderContext);

    if (context === undefined)
        throw new Error("useTheme must be used within a ThemeProvider");

    return context;
};
