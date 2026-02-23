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
