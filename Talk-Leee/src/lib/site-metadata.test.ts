import assert from "node:assert/strict";
import test from "node:test";

import sitemap from "@/app/sitemap";
import fs from "node:fs";
import path from "node:path";

import { SHARE_IMAGE, SITE_NAME, SITE_URL, sharedMetadata } from "./site-metadata";

test("the shared metadata origin is the one the sitemap already uses", () => {
    const first = sitemap()[0];
    assert.equal(new URL(first.url).origin, SITE_URL);
    assert.equal(sharedMetadata.metadataBase.origin, SITE_URL);
});

test("every route inherits a relative canonical so Next resolves it to its own trailing-slash URL", () => {
    assert.equal(sharedMetadata.alternates.canonical, "./");
    assert.equal(sharedMetadata.openGraph.url, "./");
});

test("Open Graph and Twitter carry type, site name and the large-image card", () => {
    assert.equal(sharedMetadata.openGraph.type, "website");
    assert.equal(sharedMetadata.openGraph.siteName, SITE_NAME);
    // summary_large_image, not summary: with an image present, summary would
    // show it as a small thumbnail instead of the full 1200 x 630 picture.
    assert.equal(sharedMetadata.twitter.card, "summary_large_image");
    // Titles and descriptions come from each page; nothing is repeated here.
    assert.equal("title" in sharedMetadata.openGraph, false);
    assert.equal("description" in sharedMetadata.openGraph, false);
});

test("the one share image is carried by Open Graph and Twitter alike and resolves to an absolute URL", () => {
    assert.deepEqual(sharedMetadata.openGraph.images, [SHARE_IMAGE]);
    assert.deepEqual(sharedMetadata.twitter.images, [SHARE_IMAGE]);
    assert.equal(SHARE_IMAGE.width, 1200);
    assert.equal(SHARE_IMAGE.height, 630);
    assert.ok(SHARE_IMAGE.alt.length > 0);
    // Root-relative, so Next joins it onto metadataBase: the emitted value is
    // the same absolute URL on every route, not one relative to the page.
    assert.ok(SHARE_IMAGE.url.startsWith("/"));
    assert.equal(new URL(SHARE_IMAGE.url, sharedMetadata.metadataBase).href, `${SITE_URL}/og-image.png`);
});

test("the share image file exists in public/ and is a 1200 x 630 PNG under 500 KB", () => {
    const file = path.join(process.cwd(), "public", SHARE_IMAGE.url);
    const buf = fs.readFileSync(file);
    assert.equal(buf.subarray(0, 8).toString("hex"), "89504e470d0a1a0a", "PNG signature");
    assert.equal(buf.readUInt32BE(16), SHARE_IMAGE.width);
    assert.equal(buf.readUInt32BE(20), SHARE_IMAGE.height);
    assert.ok(buf.length < 500 * 1024, `${buf.length} bytes`);
});
