import { useEffect, useRef, useState } from 'react';
import { Download, Loader2, Play, XCircle } from 'lucide-react';
import { audioExtension } from '../lib/audio-filename';
import { api } from '../lib/api';
import { useAuthGeneration } from '../lib/use-auth-generation';

interface AdminMediaPlayerProps {
    /** Exact recording/feedback identity, independent of the download label. */
    resourceId: string;
    load: () => Promise<Blob>;
    /** Download name without extension; the extension comes from the real audio format. */
    filenameBase: string;
    /** Stored MIME type of the object (preferred), e.g. `audio/mpeg`. */
    mimeType?: string | null;
    /** Used only when neither the stored nor the downloaded MIME type is recognised. */
    fallbackExtension: string;
    disabled?: boolean;
    compact?: boolean;
}

export function AdminMediaPlayer(props: AdminMediaPlayerProps) {
    const session = useAuthGeneration();
    return <OwnedMediaPlayer key={`${session}:${props.resourceId}:${Boolean(props.disabled)}`}
        {...props} session={session} />;
}

function OwnedMediaPlayer({
    load,
    filenameBase,
    mimeType,
    fallbackExtension,
    disabled = false,
    compact = false,
    session,
}: AdminMediaPlayerProps & { session: number }) {
    const [objectUrl, setObjectUrl] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [playbackFailed, setPlaybackFailed] = useState(false);
    const blobTypeRef = useRef('');
    const lifetime = useRef(0);
    const mounted = useRef(false);
    const urlRef = useRef<string | null>(null);
    const inFlight = useRef<Promise<string | null> | null>(null);
    const downloading = useRef(false);

    useEffect(() => {
        mounted.current = true;
        lifetime.current += 1;
        return () => {
            mounted.current = false;
            lifetime.current += 1;
            if (urlRef.current) URL.revokeObjectURL(urlRef.current);
            urlRef.current = null;
            inFlight.current = null;
        };
    }, []);

    const owns = (generation: number) => mounted.current
        && generation === lifetime.current && session === api.getAuthGeneration();

    const ensureLoaded = async (): Promise<string | null> => {
        const generation = lifetime.current;
        if (!owns(generation)) return null;
        if (urlRef.current) return urlRef.current;
        if (inFlight.current) return inFlight.current;
        setLoading(true);
        setError(null);
        setPlaybackFailed(false);
        const pending = (async () => {
            try {
                const blob = await load();
                if (!owns(generation)) return null;
                const url = URL.createObjectURL(blob);
                if (!owns(generation)) {
                    URL.revokeObjectURL(url);
                    return null;
                }
                blobTypeRef.current = blob.type;
                urlRef.current = url;
                setObjectUrl(url);
                return url;
            } catch (caught) {
                if (owns(generation)) setError(caught instanceof Error ? caught.message : 'Could not load audio');
                return null;
            } finally {
                if (owns(generation)) setLoading(false);
            }
        })();
        inFlight.current = pending;
        try {
            return await pending;
        } finally {
            if (inFlight.current === pending) inFlight.current = null;
        }
    };

    const reloadAudio = async () => {
        if (!owns(lifetime.current) || inFlight.current) return;
        lifetime.current += 1;
        if (urlRef.current) URL.revokeObjectURL(urlRef.current);
        urlRef.current = null;
        blobTypeRef.current = '';
        setObjectUrl(null);
        await ensureLoaded();
    };

    const playbackError = () => {
        if (!owns(lifetime.current) || !objectUrl || objectUrl !== urlRef.current) return;
        // A successful download does not prove that the browser can decode it.
        // Keep these bytes available for explicit download until reload/retire.
        setPlaybackFailed(true);
        setError('Audio could not be played. Reload the audio to try again, or download the file.');
    };

    const download = async () => {
        const generation = lifetime.current;
        if (!owns(generation) || downloading.current) return;
        downloading.current = true;
        try {
            const url = await ensureLoaded();
            if (!url || !owns(generation)) return;
            const anchor = document.createElement('a');
            anchor.href = url;
            const extension = audioExtension(mimeType) ?? audioExtension(blobTypeRef.current) ?? fallbackExtension;
            anchor.download = `${filenameBase}.${extension}`;
            document.body.appendChild(anchor);
            anchor.click();
            anchor.remove();
        } finally {
            downloading.current = false;
        }
    };

    if (disabled) {
        return <span className="media-unavailable"><XCircle size={14} /> Unavailable</span>;
    }

    return (
        <div className={`admin-media-player ${compact ? 'compact' : ''}`}>
            {objectUrl && !playbackFailed ? (
                <audio key={objectUrl} controls preload="metadata" src={objectUrl} onError={playbackError}>
                    Your browser does not support audio playback.
                </audio>
            ) : (
                <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => void (error ? reloadAudio() : ensureLoaded())}
                    disabled={loading}
                >
                    {loading ? <Loader2 size={14} className="spinning" /> : <Play size={14} />}
                    {loading ? 'Loading' : error ? 'Reload audio' : 'Play'}
                </button>
            )}
            <button
                type="button"
                className="btn btn-secondary btn-sm icon-only"
                onClick={() => void download()}
                disabled={loading}
                title="Download audio"
                aria-label="Download audio"
            >
                <Download size={14} />
            </button>
            {error && <span className="media-load-error" role="alert">{error}</span>}
        </div>
    );
}
