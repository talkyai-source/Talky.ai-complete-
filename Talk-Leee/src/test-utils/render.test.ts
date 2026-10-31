import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { cleanup } from "@testing-library/react";
import { createElement } from "react";

import { ensureDom } from "@/test-utils/dom";
import { renderWithQueryClient } from "@/test-utils/render";

ensureDom();

afterEach(cleanup);

test("the test query client does not schedule five-minute garbage-collection timers", () => {
    const { qc } = renderWithQueryClient(createElement("div"));

    assert.equal(qc.getDefaultOptions().queries?.gcTime, Infinity);
    assert.equal(qc.getDefaultOptions().mutations?.gcTime, Infinity);
});
