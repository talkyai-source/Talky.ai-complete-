import { chatReducer, initialChatState, type ChatEvent, type ChatState } from "@/lib/support-chat-state";
import type { PickedAttachment } from "@/lib/support-attachments";
import type { TicketDraft } from "@/lib/support-ticket-draft";

/*
 * The drawer's state outlives any one page: each dashboard route mounts its
 * own layout, so component state would reset on navigation. This store keeps
 * the open flag, the chat machine, the ticket draft and the picked files in
 * module scope; `inFlight` remembers which pending write is executing so a
 * remount never sends it twice.
 */

export type SupportDrawerState = {
    open: boolean;
    chat: ChatState;
    draft: TicketDraft | null;
    attachments: PickedAttachment[];
    /** The request_id of the write currently executing, if any. */
    inFlight: string | null;
};

type Listener = () => void;

const initial: SupportDrawerState = { open: false, chat: initialChatState, draft: null, attachments: [], inFlight: null };

export class SupportChatStore {
    private listeners = new Set<Listener>();
    private state: SupportDrawerState = initial;

    subscribe(listener: Listener) {
        this.listeners.add(listener);
        return () => { this.listeners.delete(listener); };
    }
    getSnapshot(): SupportDrawerState {
        return this.state;
    }
    private set(next: Partial<SupportDrawerState>) {
        this.state = { ...this.state, ...next };
        for (const listener of this.listeners) listener();
    }
    setOpen(open: boolean) {
        if (open !== this.state.open) this.set({ open });
    }
    dispatch(event: ChatEvent) {
        const chat = chatReducer(this.state.chat, event);
        if (chat === this.state.chat) return;
        const resetForm = event.type === "reset" || (event.type === "resumed" && chat.phase !== "drafting_ticket");
        this.set({ chat, ...(resetForm ? { draft: null, attachments: [] } : {}) });
    }
    setDraft(draft: TicketDraft | null) {
        this.set({ draft });
    }
    setAttachments(update: PickedAttachment[] | ((current: PickedAttachment[]) => PickedAttachment[])) {
        this.set({ attachments: typeof update === "function" ? update(this.state.attachments) : update });
    }
    /** Claim a pending write; false when that id is already executing. */
    beginWrite(requestId: string): boolean {
        if (this.state.inFlight === requestId) return false;
        this.set({ inFlight: requestId });
        return true;
    }
    endWrite(requestId: string) {
        if (this.state.inFlight === requestId) this.set({ inFlight: null });
    }
    reset() {
        this.set({ ...initial, open: this.state.open });
    }
    resetForTests() {
        this.listeners.clear();
        this.state = initial;
    }
}

export const supportChatStore = new SupportChatStore();
export const EMPTY_SUPPORT_DRAWER_STATE = initial;
