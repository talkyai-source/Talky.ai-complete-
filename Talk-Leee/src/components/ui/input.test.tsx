import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import React, { useState } from "react";
import { cleanup, screen, fireEvent, render } from "@testing-library/react";
import { Input } from "@/components/ui/input";
import { ensureDom } from "@/test-utils/dom";

ensureDom();
afterEach(() => cleanup());

test("Input updates value", async () => {
    const TestComponent = () => {
        const [val, setVal] = useState("");
        return <Input value={val} onChange={(e) => setVal(e.target.value)} aria-label="test-input" />;
    };

    render(<TestComponent />);
    const input = screen.getByLabelText("test-input");
    
    fireEvent.change(input, { target: { value: "hello" } });
    
    // Check value
    assert.equal((input as HTMLInputElement).value, "hello");
});

test("Input text is 16px on phones so iOS does not zoom on focus, 14px from md up", () => {
    render(<Input aria-label="size-input" />);
    const classes = screen.getByLabelText("size-input").className.split(/\s+/);
    assert.ok(classes.includes("text-base"));
    assert.ok(classes.includes("md:text-sm"));
    assert.equal(classes.includes("text-sm"), false);
});
