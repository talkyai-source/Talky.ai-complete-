import { useEffect, useRef, useState } from 'react';

// Cloudflare Turnstile, explicit rendering. Only ever mounted when the build has a
// site key (VITE_TURNSTILE_SITE_KEY); without one, nothing here runs or loads.
const SCRIPT_SRC = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';

interface TurnstileApi {
    render: (container: HTMLElement, options: Record<string, unknown>) => string | undefined;
    reset: (widgetId: string) => void;
    remove: (widgetId: string) => void;
}
const loadedApi = () => (window as unknown as { turnstile?: TurnstileApi }).turnstile;

/** Calls `ready` once Cloudflare's script is usable, or `failed` if it cannot load. Returns the cleanup. */
function whenLoaded(ready: (api: TurnstileApi) => void, failed: () => void): () => void {
    const api = loadedApi();
    if (api) { ready(api); return () => undefined; }
    // One script per page: a remount (or StrictMode's second effect) reuses the tag.
    let script = document.querySelector<HTMLScriptElement>(`script[src="${SCRIPT_SRC}"]`);
    if (!script) {
        script = document.createElement('script');
        script.src = SCRIPT_SRC;
        script.async = true;
        script.defer = true;
        document.head.appendChild(script);
    }
    const tag = script;
    const onLoad = () => { const loaded = loadedApi(); if (loaded) ready(loaded); else failed(); };
    // Drop a failed tag so the next mount tries the download again.
    const onError = () => { tag.remove(); failed(); };
    tag.addEventListener('load', onLoad);
    tag.addEventListener('error', onError);
    return () => { tag.removeEventListener('load', onLoad); tag.removeEventListener('error', onError); };
}

interface TurnstileWidgetProps {
    siteKey: string;
    /** The backend checks the token's action, e.g. "login". */
    action: string;
    /** Receives each fresh token, and null when it expires or the challenge fails. Pass a stable function (a state setter). */
    onToken: (token: string | null) => void;
    /** Bump after every attempt: a token is single-use, so the widget must issue another. */
    resetCount: number;
}

export function TurnstileWidget({ siteKey, action, onToken, resetCount }: TurnstileWidgetProps) {
    const container = useRef<HTMLDivElement>(null);
    const widgetId = useRef<string | null>(null);
    const handledResets = useRef(resetCount);
    const [unavailable, setUnavailable] = useState(false);

    useEffect(() => {
        const element = container.current;
        if (!element) return undefined;
        const stop = whenLoaded((api) => {
            // render() throws, or returns no id, on bad input such as a malformed site key.
            // Without a widget no token can ever arrive: say so, and never blank the sign-in page.
            try {
                const id = api.render(element, {
                    sitekey: siteKey,
                    action,
                    theme: 'light',
                    size: 'flexible',
                    callback: (token: string) => onToken(token),
                    'expired-callback': () => onToken(null),
                    'timeout-callback': () => onToken(null),
                    'error-callback': () => onToken(null),
                });
                if (id === undefined) setUnavailable(true); else widgetId.current = id;
            } catch { setUnavailable(true); }
        }, () => setUnavailable(true));
        return () => {
            stop();
            if (widgetId.current !== null) loadedApi()?.remove(widgetId.current);
            widgetId.current = null;
        };
    }, [siteKey, action, onToken]);

    useEffect(() => {
        if (handledResets.current === resetCount) return;
        handledResets.current = resetCount;
        if (widgetId.current !== null) loadedApi()?.reset(widgetId.current);
    }, [resetCount]);

    return <>
        <div ref={container} />
        {unavailable && <div className="login-error" role="alert">
            Bot verification could not be loaded. Allow challenges.cloudflare.com in this browser, then reload the page.
        </div>}
    </>;
}
