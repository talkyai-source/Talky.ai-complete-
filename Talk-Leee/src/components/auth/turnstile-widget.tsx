"use client";

import { useEffect, useRef, useState } from "react";

// Cloudflare Turnstile bot check for the sign-in, sign-up and forgot-password
// forms (QA F4-12). The backend reads the token from the JSON body as
// `turnstile_token` and also compares the `action` Cloudflare echoes back, so
// every form names its own. It is off unless NEXT_PUBLIC_TURNSTILE_SITE_KEY is
// set: with no key there is no script, no widget, no token and no extra UI.

export type TurnstileAction = "login" | "signup" | "forgot_password";

const API_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
const API_SCRIPT = 'script[src^="https://challenges.cloudflare.com/turnstile/v0/api.js"]';

type TurnstileApi = {
    render: (container: HTMLElement, options: Record<string, unknown>) => string | undefined;
    remove: (widgetId: string) => void;
};
const turnstileApi = () => (window as unknown as { turnstile?: TurnstileApi }).turnstile;

/** Read when called, not at import, so it follows the build-time value. Blank means off. */
export function turnstileSiteKey(): string | undefined {
    return process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY?.trim() || undefined;
}

export function TurnstileWidget({ action, onToken }: {
    action: TurnstileAction;
    /** The solved token, or null once it can no longer be used. */
    onToken: (token: string | null) => void;
}) {
    const siteKey = turnstileSiteKey();
    const host = useRef<HTMLDivElement>(null);
    // A parent usually passes a new callback every render. Keep the latest in a
    // ref so that does not rebuild the widget and throw a solved challenge away.
    const latestOnToken = useRef(onToken);
    useEffect(() => { latestOnToken.current = onToken; });

    useEffect(() => {
        const container = host.current;
        if (!siteKey || !container) return;
        let live = true;
        let widgetId: string | undefined;
        const give = (token: string | null) => { if (live) latestOnToken.current(token); };
        const draw = () => {
            const api = turnstileApi();
            if (!live || !api || widgetId !== undefined) return;
            widgetId = api.render(container, {
                sitekey: siteKey,
                action,
                theme: "auto",
                size: "flexible",
                callback: give,
                "expired-callback": () => give(null),
                "error-callback": () => give(null),
            });
        };

        let script: HTMLScriptElement | null = null;
        if (turnstileApi()) {
            draw();
        } else {
            script = document.querySelector<HTMLScriptElement>(API_SCRIPT);
            if (!script) {
                script = document.createElement("script");
                script.src = API_SRC;
                script.async = true;
                document.head.appendChild(script);
            }
            script.addEventListener("load", draw);
        }

        return () => {
            // Once the widget is gone nothing keeps its token fresh, so withdraw it.
            give(null);
            live = false;
            script?.removeEventListener("load", draw);
            if (widgetId !== undefined) {
                try { turnstileApi()?.remove(widgetId); } catch { /* already gone */ }
            }
        };
    }, [siteKey, action]);

    return siteKey ? <div ref={host} className="flex w-full justify-center" /> : null;
}

/**
 * What a form needs: the widget to draw, whether submitting must wait for it,
 * and `run` to send the request that spends a token. Cloudflare accepts a token
 * once, so `run` hands it to that one request and starts a fresh challenge when
 * the request settles, whether it worked or not. With no site key `run` just
 * calls `send(undefined)` and nothing else happens.
 */
export function useTurnstile(action: TurnstileAction) {
    const enabled = turnstileSiteKey() !== undefined;
    const [token, setToken] = useState<string | null>(null);
    const [round, setRound] = useState(0);
    return {
        widget: enabled ? <TurnstileWidget key={round} action={action} onToken={setToken} /> : null,
        pending: enabled && token === null,
        async run<T>(send: (token: string | undefined) => Promise<T>): Promise<T> {
            // Unsolved (or off): send without a token and leave the widget alone.
            if (token === null) return send(undefined);
            setToken(null);
            try {
                return await send(token);
            } finally {
                setRound((n) => n + 1);
            }
        },
    };
}
