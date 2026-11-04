/**
 * ONE phone parser for every contact entry point (contacts page upload card,
 * "Check the file first" mapper, campaign Import CSV modal, both Add/Edit
 * contact forms). Built on libphonenumber-js; nothing here adds a "+" or a
 * country code, and nothing keeps digits the library could not parse.
 *
 *   "+…"       parsed as international, the selected country is ignored.
 *   national   parsed with the selected country; with none selected the row
 *              is an error, never a guess.
 *   valid      only when libphonenumber-js reports the number as valid; short
 *              numbers and 4–5 digit extensions are rejected.
 *
 * The "max" metadata build is used so isValid() checks the real number
 * ranges, not just the length. It is passed to the core API explicitly:
 * the `libphonenumber-js/max` entry imports its metadata as an ES default
 * export, which the test loader (tsx) hands back wrapped in `{ default }`;
 * the guard below accepts both shapes.
 */
import {
    getCountries as getCountriesWith,
    getCountryCallingCode as getCountryCallingCodeWith,
    parsePhoneNumberFromString as parseWith,
    type CountryCode,
    type MetadataJson,
} from "libphonenumber-js/core";
import metadataJson from "libphonenumber-js/metadata.max.json";

const metadata: MetadataJson =
    (metadataJson as unknown as { default?: MetadataJson }).default ?? (metadataJson as MetadataJson);

export type { CountryCode };

export const SELECT_COUNTRY_ERROR = "Select a country for numbers without a + country code.";
export const INVALID_PHONE_ERROR = "Not a valid phone number.";
export const PHONE_HELP_TEXT =
    "Numbers starting with + are used as written. For numbers without a country code, select the country first.";
export const COUNTRY_SELECT_LABEL = "Default country";
export const COUNTRY_SELECT_PLACEHOLDER = "Select country";

/** "" = no country chosen (the starting value of every selector). */
export type CountrySelection = CountryCode | "";

export type PhoneParseResult =
    | { ok: true; e164: string }
    | { ok: false; error: string };

/** The only characters a typed number may carry besides digits and a leading
 *  "+": spaces, dashes, dots and brackets. Anything else makes it invalid. */
const IGNORED_CHARS = /[\s\-.()[\]]/g;

export function parsePhoneNumber(raw: string, country: CountrySelection): PhoneParseResult {
    const compact = (raw ?? "").replace(IGNORED_CHARS, "");
    if (!/^\+?\d+$/.test(compact)) return { ok: false, error: INVALID_PHONE_ERROR };
    let parsed;
    if (compact.startsWith("+")) {
        parsed = parseWith(compact, { extract: false }, metadata);
    } else {
        if (!country) return { ok: false, error: SELECT_COUNTRY_ERROR };
        parsed = parseWith(compact, { defaultCountry: country, extract: false }, metadata);
    }
    if (!parsed || !parsed.isValid()) return { ok: false, error: INVALID_PHONE_ERROR };
    return { ok: true, e164: parsed.number };
}

/** "Saves as {E.164}" line text for a valid number. */
export function savesAsText(e164: string): string {
    return `Saves as ${e164}`;
}

export type CountryOption = { code: CountryCode; label: string };

let countryOptionsCache: CountryOption[] | null = null;

/** Every country libphonenumber-js supports, labelled with the English region
 *  name from Intl.DisplayNames plus the calling code, e.g. "United Kingdom (+44)". */
export function countryOptions(): CountryOption[] {
    if (countryOptionsCache) return countryOptionsCache;
    const names = new Intl.DisplayNames(["en"], { type: "region" });
    const regionName = (code: CountryCode): string => {
        try {
            return names.of(code) ?? code;
        } catch {
            return code;
        }
    };
    countryOptionsCache = getCountriesWith(metadata)
        .map((code) => ({ code, label: `${regionName(code)} (+${getCountryCallingCodeWith(code, metadata)})` }))
        .sort((a, b) => a.label.localeCompare(b.label, "en"));
    return countryOptionsCache;
}
