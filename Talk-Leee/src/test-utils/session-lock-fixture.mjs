// Explicit supported-browser fixture for JSDOM, which has no Web Locks API.
// Models only Admin's exclusive request/signal contract. Real browser cookie
// effects and unavailable native capabilities require separate controls.
export function installSessionLocks(navigator) {
    const queues = new Map(); const names = [];
    Object.defineProperty(navigator, 'locks', { configurable: true, value: {
        request(name, options, callback) {
            names.push(name);
            const queue = queues.get(name) ?? { active: false, waiting: [] };
            queues.set(name, queue);
            return new Promise((resolve, reject) => {
                const entry = { callback, resolve, reject, abort: null };
                const pump = () => {
                    if (queue.active || !queue.waiting.length) return;
                    const next = queue.waiting.shift(); queue.active = true;
                    next.detach();
                    Promise.resolve().then(() => next.callback({ name, mode: 'exclusive' }))
                        .then(next.resolve, next.reject).finally(() => { queue.active = false; pump(); });
                };
                entry.abort = () => {
                    const at = queue.waiting.indexOf(entry);
                    if (at >= 0) { queue.waiting.splice(at, 1); entry.detach(); reject(new DOMException('Aborted', 'AbortError')); }
                };
                entry.detach = () => options.signal?.removeEventListener('abort', entry.abort);
                if (options.signal?.aborted) { reject(new DOMException('Aborted', 'AbortError')); return; }
                options.signal?.addEventListener('abort', entry.abort, { once: true });
                queue.waiting.push(entry); pump();
            });
        },
    } });
    return names;
}
