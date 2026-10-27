import assert from "node:assert/strict";
import test from "node:test";

import sitemap from "@/app/sitemap";
import { SITE_NAME, SITE_URL, sharedMetadata } from "./site-metadata";

test("the shared metadata origin is the one the sitemap already uses", () => {
    const first = sitemap()[0];
    assert.equal(new URL(first.url).origin, SITE_URL);
    assert.equal(sharedMetadata.metadataBase.origin, SITE_URL);
});

test("every route inherits a relative canonical so Next resolves it to its own trailing-slash URL", () => {
    assert.equal(sharedMetadata.alternates.canonical, "./");
    assert.equal(sharedMetadata.openGraph.url, "./");
});

test("Open Graph and Twitter carry type, site name and card, and no invented image", () => {
    assert.equal(sharedMetadata.openGraph.type, "website");
    assert.equal(sharedMetadata.openGraph.siteName, SITE_NAME);
    assert.equal(sharedMetadata.twitter.card, "summary");
    assert.equal("images" in sharedMetadata.openGraph, false);
    assert.equal("images" in sharedMetadata.twitter, false);
    // Titles and descriptions come from each page; nothing is repeated here.
    assert.equal("title" in sharedMetadata.openGraph, false);
    assert.equal("description" in sharedMetadata.openGraph, false);
});
