import { useEffect, useState } from 'react';
import { Download, Loader2, Play, XCircle } from 'lucide-react';

interface AdminMediaPlayerProps {
    load: () => Promise<Blob>;
    filename: string;
    disabled?: boolean;
    compact?: boolean;
}

export function AdminMediaPlayer({
    load,
    filename,
    disabled = false,
    compact = false,
}: AdminMediaPlayerProps) {
    const [objectUrl, setObjectUrl] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        return () => {
            if (objectUrl) URL.revokeObjectURL(objectUrl);
        };
    }, [objectUrl]);

    const ensureLoaded = async (): Promise<string | null> => {
        if (objectUrl) return objectUrl;
        setLoading(true);
        setError(null);
        try {
            const blob = await load();
            const url = URL.createObjectURL(blob);
            setObjectUrl(url);
            return url;
        } catch (caught) {
            setError(caught instanceof Error ? caught.message : 'Could not load audio');
            return null;
        } finally {
            setLoading(false);
        }
    };

    const download = async () => {
        const url = await ensureLoaded();
        if (!url) return;
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = filename;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
    };

    if (disabled) {
        return <span className="media-unavailable"><XCircle size={14} /> Unavailable</span>;
    }

    return (
        <div className={`admin-media-player ${compact ? 'compact' : ''}`}>
            {objectUrl ? (
                <audio controls preload="metadata" src={objectUrl}>
                    Your browser does not support audio playback.
                </audio>
            ) : (
                <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => void ensureLoaded()}
                    disabled={loading}
                >
                    {loading ? <Loader2 size={14} className="spinning" /> : <Play size={14} />}
                    {loading ? 'Loading' : 'Play'}
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
