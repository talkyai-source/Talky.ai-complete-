/**
 * Fills the shared "Permission to call these contacts" block the way a person
 * would, so tests of every add / import screen use the same values.
 */
import { fireEvent, screen } from "@testing-library/react";

import { CONTACT_PERMISSION_TEXT } from "@/lib/contact-permission";

export const PERMISSION_SOURCE = "Website sign-up form, March 2026";
export const PERMISSION_DATE = "2026-03-14";

/** What the block above produces, as the backend expects it. */
export const EXPECTED_PERMISSION = {
    basis: "express_consent",
    source: PERMISSION_SOURCE,
    permission_date: PERMISSION_DATE,
    statement_version: "2026-11-v1",
} as const;

export function fillContactPermission(overrides: { source?: string; date?: string; skipConfirm?: boolean } = {}) {
    fireEvent.click(screen.getByRole("radio", { name: "They agreed to be called" }));
    fireEvent.change(screen.getByLabelText(CONTACT_PERMISSION_TEXT.sourceLabel), { target: { value: overrides.source ?? PERMISSION_SOURCE } });
    fireEvent.change(screen.getByLabelText(CONTACT_PERMISSION_TEXT.dateLabel), { target: { value: overrides.date ?? PERMISSION_DATE } });
    if (!overrides.skipConfirm) fireEvent.click(screen.getByRole("checkbox", { name: CONTACT_PERMISSION_TEXT.confirmLabel }));
}
