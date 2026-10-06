"use client";
import { Select } from "@/components/ui/select";
import { DEFAULT_REALTIME_PROMPT, type CampaignVoiceSettingsValue } from "./types";
type Prompt = NonNullable<CampaignVoiceSettingsValue["realtime_prompt"]>;

export function RealtimePromptEditor({ value = DEFAULT_REALTIME_PROMPT, onChange }: { value?: Prompt; onChange: (value: Prompt) => void }) {
    const cls = "mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm";
    return <div className="space-y-4">
        <label className="block text-sm font-medium">Realtime persona
            <Select className="mt-1" selectClassName="h-auto px-3 py-2 text-sm rounded-md border bg-background" ariaLabel="Realtime persona" value={value.persona ?? "assistant"} onChange={(next) => onChange({ ...value, persona: next as Prompt["persona"] })}>
                <option value="assistant">General assistant</option><option value="sales">Sales</option><option value="support">Support</option><option value="receptionist">Receptionist</option>
            </Select>
        </label>
        <label className="block text-sm font-medium">Realtime goal
            <textarea className={cls} maxLength={1000} value={value.goal} onChange={(e) => onChange({ ...value, goal: e.target.value })} />
        </label>
        <label className="block text-sm font-medium">Realtime instructions
            <textarea className={cls} rows={6} maxLength={6000} value={value.instructions} placeholder="Conversation flow, questions to ask, and business-specific guidance." onChange={(e) => onChange({ ...value, instructions: e.target.value })} />
        </label>
        <label className="block text-sm font-medium">Realtime opening greeting (optional)
            <textarea className={cls} maxLength={500} value={value.opening_greeting} onChange={(e) => onChange({ ...value, opening_greeting: e.target.value })} />
        </label>
    </div>;
}
