"use client";

import { useMemo } from "react";

import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import {
    COUNTRY_SELECT_LABEL,
    COUNTRY_SELECT_PLACEHOLDER,
    countryOptions,
    type CountrySelection,
} from "@/lib/phone-number";

/** The "Default country" selector every contact entry point carries. Starts
 *  with no country; applies only to numbers typed without a "+". The choice
 *  is component state, never stored, so it is not remembered between visits. */
export function DefaultCountrySelect({
    id,
    value,
    onChange,
    disabled,
    className,
}: {
    id: string;
    value: CountrySelection;
    onChange: (next: CountrySelection) => void;
    disabled?: boolean;
    className?: string;
}) {
    const options = useMemo(() => countryOptions(), []);
    return (
        <div className={className}>
            <Label htmlFor={id}>{COUNTRY_SELECT_LABEL}</Label>
            <Select
                id={id}
                ariaLabel={COUNTRY_SELECT_LABEL}
                value={value}
                onChange={(next) => onChange(next as CountrySelection)}
                disabled={disabled}
                className="mt-1"
            >
                <option value="">{COUNTRY_SELECT_PLACEHOLDER}</option>
                {options.map((option) => (
                    <option key={option.code} value={option.code}>{option.label}</option>
                ))}
            </Select>
        </div>
    );
}
