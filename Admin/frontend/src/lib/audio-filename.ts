// File extension for a downloaded audio object, from its MIME type. A wrong
// extension makes players and evidence tools misidentify the codec.
const AUDIO_EXTENSIONS: Record<string, string> = {
    'audio/mpeg': 'mp3',
    'audio/mp3': 'mp3',
    'audio/ogg': 'ogg',
    'application/ogg': 'ogg',
    'audio/opus': 'opus',
    'audio/mp4': 'm4a',
    'audio/x-m4a': 'm4a',
    'audio/m4a': 'm4a',
    'audio/aac': 'aac',
    'audio/wav': 'wav',
    'audio/x-wav': 'wav',
    'audio/wave': 'wav',
    'audio/vnd.wave': 'wav',
    'audio/webm': 'webm',
    'audio/flac': 'flac',
};

/** Extension for `mime` (parameters such as `;codecs=opus` ignored), or null when unknown. */
export function audioExtension(mime: string | null | undefined): string | null {
    if (!mime) return null;
    const base = mime.split(';')[0].trim().toLowerCase();
    return AUDIO_EXTENSIONS[base] ?? null;
}
