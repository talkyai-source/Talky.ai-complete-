/**
 * 2.4 acceptance fixtures (AC-PH) shared by every entry-point test so the
 * value each path sends is asserted against ONE constant.
 *
 * The UK number is 07400 123456: libphonenumber-js 1.13.15 reports Ofcom's
 * reserved drama range 07700 900xxx as not valid, and the shared module only
 * returns an E.164 value the library reports as valid (Task 1).
 */
import { fireEvent, screen } from "@testing-library/react";

export const UK_NATIONAL = "07400 123456";
export const UK_E164 = "+447400123456";
export const BAD_NUMBER = "12345";
export const UK_OPTION_LABEL = "United Kingdom (+44)";

/** Open the shared Select whose trigger has this id and pick the UK option. */
export async function chooseUnitedKingdom(triggerId: string) {
    const trigger = document.getElementById(triggerId);
    if (!trigger) throw new Error(`no country selector #${triggerId}`);
    fireEvent.click(trigger);
    const option = await screen.findByRole("option", { name: UK_OPTION_LABEL });
    fireEvent.click(option);
}

export function csvFile(text: string, name = "contacts.csv"): File {
    return new File([text], name, { type: "text/csv" });
}
