
import { describe, it, afterEach } from "node:test";
import assert from "node:assert";
import { render, screen, cleanup } from "@testing-library/react";
import React from "react";
import { ContactSection } from "./contact-section";

describe("ContactSection", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders the contact form and info section", () => {
    render(React.createElement(ContactSection));
    
    // Check for header
    assert.ok(screen.getByText("Contact Us"));
    
    // Check for form fields
    assert.ok(screen.getByLabelText("Full Name"));
    assert.ok(screen.getByLabelText("Email Address"));
    assert.ok(screen.getByLabelText("Company"));
    assert.ok(screen.getByLabelText("Message"));
    
    // Check for contact info
    assert.ok(screen.getByText("Get in Touch"));
    assert.ok(screen.getByText("contact@talk-lee.com"));
    assert.ok(screen.getByText("+1 (555) 123-4567"));
  });
});
