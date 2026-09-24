/**
 * First-party transport shim for the SpatialReal (@spatialwalk/avatarkit) SDK.
 *
 * The SDK talks to hardcoded `*.spatialwalk.*` hosts for its model CDN, API,
 * config bootstrap, telemetry, and a realtime driving WebSocket. Network-level
 * DNS/ad filters (NextDNS, Pi-hole, AdGuard, "family" DNS, some VPNs) and
 * browser ad-block extensions frequently block those third-party hosts — the
 * SDK then can't download the model and the avatar renders as "unavailable"
 * while the call falls back to audio-only.
 *
 * This module rewrites those requests to same-origin paths so the browser only
 * ever contacts our own domain (which a filter can't block without breaking the
 * whole app the user is already on):
 *   - HTTP(S) → `/_spatial/<key>/...`   (proxied by next.config.ts rewrites)
 *   - WSS     → `/_spatial-ws/<key>/...` (proxied by the nginx sidecar)
 *
 * It mirrors the existing PostHog `/ingest` and Sentry `/monitoring` tunnels in
 * this app. Only spatialwalk hosts are touched; every other fetch/WebSocket
 * (including our own same-origin signaling WS) passes through untouched.
 *
 * Import this module for its side effect BEFORE the avatarkit SDK is loaded.
 */

// Upstream host -> path key used by both the Next rewrites and the nginx WS proxy.
const HOST_TO_KEY: Record<string, string> = {
    'cdn.spatialwalk.cloud': 'cdn',
    'api.intl.spatialwalk.cloud': 'api',
    'config.spatialwalk.top': 'config',
    'i.spatialwalk.ai': 'i',
    'hogtool.spatialwalk.ai': 'hog',
};

function keyForHost(host: string): string | null {
    return HOST_TO_KEY[host] ?? null;
}

/** Rewrite a spatialwalk absolute URL to a same-origin first-party path. */
function rewriteUrl(raw: string): string {
    try {
        const u = new URL(raw, window.location.href);
        const key = keyForHost(u.hostname);
        if (!key) return raw;
        const isWs = u.protocol === 'ws:' || u.protocol === 'wss:';
        const prefix = isWs ? '/_spatial-ws/' : '/_spatial/';
        const proto = isWs
            ? (window.location.protocol === 'https:' ? 'wss:' : 'ws:')
            : window.location.protocol;
        return `${proto}//${window.location.host}${prefix}${key}${u.pathname}${u.search}`;
    } catch {
        return raw;
    }
}

let installed = false;

export function installSpatialProxy(): void {
    if (installed || typeof window === 'undefined') return;
    installed = true;

    // ── fetch ──
    const origFetch = window.fetch.bind(window);
    window.fetch = function patchedFetch(input: RequestInfo | URL, init?: RequestInit) {
        try {
            if (typeof input === 'string' || input instanceof URL) {
                const rewritten = rewriteUrl(String(input));
                if (rewritten !== String(input)) return origFetch(rewritten, init);
            } else if (input instanceof Request) {
                const rewritten = rewriteUrl(input.url);
                if (rewritten !== input.url) {
                    // Rebuild the Request against the rewritten URL, preserving method/body/etc.
                    return origFetch(new Request(rewritten, input), init);
                }
            }
        } catch {
            /* fall through to the original */
        }
        return origFetch(input as RequestInfo, init);
    };

    // ── WebSocket ──
    const OrigWebSocket = window.WebSocket;
    const PatchedWebSocket = function (
        this: WebSocket,
        url: string | URL,
        protocols?: string | string[],
    ) {
        const rewritten = rewriteUrl(String(url));
        return protocols !== undefined
            ? new OrigWebSocket(rewritten, protocols)
            : new OrigWebSocket(rewritten);
    } as unknown as typeof WebSocket;
    PatchedWebSocket.prototype = OrigWebSocket.prototype;
    // Copy the readonly ready-state constants across (cast: TS marks them readonly).
    const wsStatics = PatchedWebSocket as unknown as Record<string, number>;
    wsStatics.CONNECTING = OrigWebSocket.CONNECTING;
    wsStatics.OPEN = OrigWebSocket.OPEN;
    wsStatics.CLOSING = OrigWebSocket.CLOSING;
    wsStatics.CLOSED = OrigWebSocket.CLOSED;
    window.WebSocket = PatchedWebSocket;
}
