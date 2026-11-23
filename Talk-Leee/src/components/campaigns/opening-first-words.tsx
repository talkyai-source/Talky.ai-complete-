import type { ReactNode } from "react";

/**
 * What an outgoing call says first, as the preview route reports it in the
 * optional `outbound_first_words` field. The server may omit the field (older
 * server), send null (incoming campaigns, Realtime voice), or send something we
 * do not recognise; all of those mean "show the plain greeting as before".
 */
export type OutboundFirstWords = {
    notice_first: string | null;
    opening: string;
    notice_in_opening: boolean;
    /** The campaign waits for the person to speak first. */
    waits_for_person: boolean;
    /** The notice as said on its own when their first words are not a greeting. */
    notice_alone: string | null;
    /** The policy asks for the notice only on calls to certain places. */
    notice_depends_on_place: boolean;
};

export function parseOutboundFirstWords(raw: unknown): OutboundFirstWords | null {
    if (!raw || typeof raw !== "object") return null;
    const value = raw as Record<string, unknown>;
    if (typeof value.opening !== "string" || !value.opening.trim()) return null;
    if (typeof value.notice_in_opening !== "boolean") return null;
    const notice = value.notice_first;
    if (notice !== null && notice !== undefined && typeof notice !== "string") return null;
    return {
        notice_first: typeof notice === "string" && notice.trim() ? notice : null,
        opening: value.opening,
        notice_in_opening: value.notice_in_opening,
        waits_for_person: value.waits_for_person === true,
        notice_alone: typeof value.notice_alone === "string" && value.notice_alone.trim() ? value.notice_alone : null,
        notice_depends_on_place: value.notice_depends_on_place === true,
    };
}

/** Inside the "How the agent will open" box. Without usable first words: exactly `greeting`. */
export function OpeningBoxText({ greeting, firstWords, outbound }: {
    greeting: ReactNode;
    firstWords: unknown;
    outbound: boolean;
}) {
    const words = outbound ? parseOutboundFirstWords(firstWords) : null;
    if (!words) return <>{greeting}</>;
    if (words.notice_first) {
        return (
            <>
                <span className="block">{`First, your recording notice, on its own: "${words.notice_first}"`}</span>
                <span className="block">{`Then the opening: "${words.opening}"`}</span>
            </>
        );
    }
    return <>{words.opening}</>;
}

/** Under the box. Renders nothing without usable first words. */
export function OpeningNotes({ firstWords, outbound }: { firstWords: unknown; outbound: boolean }) {
    const words = outbound ? parseOutboundFirstWords(firstWords) : null;
    if (!words) return null;
    const noticeSaid = Boolean(words.notice_first) || words.notice_in_opening;
    return (
        <div className="mt-1 space-y-1 text-xs text-muted-foreground">
            {noticeSaid ? (
                <p>
                    {words.notice_in_opening ? `"on a recorded line" is this account's recording notice. ` : ""}
                    {words.notice_depends_on_place
                        ? "Your recording policy asks for the notice only on calls to certain places. On other calls the agent says the opening without it. You set this in Settings, under Recording policy."
                        : "The recording notice is said on calls where your recording policy requires one. You set it in Settings, under Recording policy."}
                </p>
            ) : null}
            {words.waits_for_person ? (
                <p>
                    {words.notice_alone
                        ? `This campaign waits for the person to speak first. If their first words are not a greeting, the agent says "${words.notice_alone}" and then answers them, without this opening.`
                        : "This campaign waits for the person to speak first. If their first words are not a greeting, the agent answers them without this opening."}
                </p>
            ) : null}
            <p>When the contact has a first name on file, the question is &quot;Am I speaking with [first name]?&quot;.</p>
        </div>
    );
}
