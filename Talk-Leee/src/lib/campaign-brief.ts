export type CampaignNextAction =
    | "schedule_callback"
    | "send_email"
    | "submit_form"
    | "transfer"
    | "end_call";

export interface CampaignBriefLeadField {
    field_key: string;
    label: string;
}

export interface CampaignBriefDraft {
    decision_maker_role: string;
    approved_next_actions: CampaignNextAction[];
    transfer_destination: string;
    opening_objective: string;
    max_objection_attempts: number;
}

export interface CampaignBrief {
    representative_name: string;
    brand: string;
    decision_maker_role: string | null;
    approved_next_actions: CampaignNextAction[];
    transfer_destination: string | null;
    required_lead_fields: CampaignBriefLeadField[];
    opening_objective: string | null;
    max_objection_attempts: number;
}

export const EMPTY_CAMPAIGN_BRIEF_DRAFT: CampaignBriefDraft = {
    decision_maker_role: "",
    approved_next_actions: [],
    transfer_destination: "",
    opening_objective: "",
    max_objection_attempts: 2,
};

export const CAMPAIGN_NEXT_ACTION_OPTIONS: ReadonlyArray<{
    value: CampaignNextAction;
    label: string;
    description: string;
}> = [
    {
        value: "schedule_callback",
        label: "Schedule callback",
        description: "Offer a specific follow-up time after the caller agrees.",
    },
    {
        value: "send_email",
        label: "Send follow-up email",
        description: "Send only after the caller confirms the address and content.",
    },
    {
        value: "submit_form",
        label: "Submit configured form",
        description: "Submit only through an available form tool with a success result.",
    },
    {
        value: "transfer",
        label: "Transfer to a human",
        description: "Attempt only when the transfer runtime is available and approved.",
    },
    {
        value: "end_call",
        label: "End call politely",
        description: "Close after the objective is complete or the caller declines.",
    },
];

const ACTION_LABELS: Record<CampaignNextAction, string> = {
    schedule_callback: "schedule a callback",
    send_email: "send a follow-up email",
    submit_form: "submit the configured form",
    transfer: "transfer to the approved destination",
    end_call: "end the call politely",
};

export function campaignBriefDraft(value?: Partial<CampaignBrief> | null): CampaignBriefDraft {
    return {
        decision_maker_role: value?.decision_maker_role?.trim() ?? "",
        approved_next_actions: [...(value?.approved_next_actions ?? [])],
        transfer_destination: value?.transfer_destination?.trim() ?? "",
        opening_objective: value?.opening_objective?.trim() ?? "",
        max_objection_attempts: value?.max_objection_attempts ?? 2,
    };
}

export function buildCampaignBrief({
    draft,
    brand,
    representativeNames,
    requiredLeadFields,
}: {
    draft: CampaignBriefDraft;
    brand: string;
    representativeNames: string[];
    requiredLeadFields: CampaignBriefLeadField[];
}): CampaignBrief {
    return {
        representative_name: representativeNames[0]?.trim() ?? "",
        brand: brand.trim(),
        decision_maker_role: draft.decision_maker_role.trim(),
        approved_next_actions: [...new Set(draft.approved_next_actions)],
        transfer_destination: draft.transfer_destination.trim(),
        required_lead_fields: requiredLeadFields.map((field) => ({
            field_key: field.field_key.trim(),
            label: field.label.trim(),
        })),
        opening_objective: draft.opening_objective.trim(),
        max_objection_attempts: draft.max_objection_attempts,
    };
}

export function campaignBriefValidation(draft: CampaignBriefDraft): string | null {
    if (!draft.opening_objective.trim()) {
        return "Add an opening objective so the representative knows what a successful first exchange should achieve.";
    }
    if (draft.opening_objective.length > 500) {
        return "Opening objective must be 500 characters or fewer.";
    }
    if (draft.decision_maker_role.length > 160) {
        return "Decision-maker role must be 160 characters or fewer.";
    }
    if (!Number.isInteger(draft.max_objection_attempts)
        || draft.max_objection_attempts < 1
        || draft.max_objection_attempts > 5) {
        return "Maximum objection attempts must be from 1 to 5.";
    }
    const transferApproved = draft.approved_next_actions.includes("transfer");
    if (transferApproved && !draft.transfer_destination.trim()) {
        return "Add a transfer destination or remove Transfer from the approved next actions.";
    }
    if (!transferApproved && draft.transfer_destination.trim()) {
        return "Select Transfer as an approved next action or clear the transfer destination.";
    }
    return null;
}

export function renderCampaignBrief(brief: CampaignBrief): string {
    const lines = ["## CAMPAIGN BRIEF"];
    if (brief.representative_name) {
        lines.push(`- Representative on this call: ${brief.representative_name}`);
    }
    if (brief.brand) lines.push(`- Brand represented: ${brief.brand}`);
    if (brief.decision_maker_role) {
        lines.push(`- Intended decision-maker role: ${brief.decision_maker_role}`);
    }
    if (brief.opening_objective) {
        lines.push(`- Opening objective: ${brief.opening_objective}`);
    }
    const actions = brief.approved_next_actions.map((action) => ACTION_LABELS[action]);
    if (actions.length) lines.push(`- Approved next actions: ${actions.join("; ")}`);
    if (brief.transfer_destination) {
        lines.push(`- Approved transfer destination: ${brief.transfer_destination}`);
    }
    if (brief.required_lead_fields.length) {
        lines.push(
            `- Required lead fields: ${brief.required_lead_fields
                .map((field) => `${field.label} (${field.field_key})`)
                .join("; ")}`,
        );
    }
    lines.push(`- Maximum objection-handling attempts: ${brief.max_objection_attempts}`);
    lines.push(
        "Only take an approved next action after its runtime tool reports success. "
        + "A configured destination does not mean transfer is currently available.",
    );
    return lines.join("\n");
}

export function campaignBriefGuidanceText(
    additionalInstructions: string,
    brief: CampaignBrief,
): string {
    return [renderCampaignBrief(brief), additionalInstructions.trim()].filter(Boolean).join("\n\n");
}
