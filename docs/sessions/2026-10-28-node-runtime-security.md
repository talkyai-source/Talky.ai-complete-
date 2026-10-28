# Node 22 runtime security pin — 28 October 2026

The fresh Ubuntu preparation helper and template now select **Node 22.23.4**
instead of 22.22.0. The installer/frontend and local-runtime CI pins match.
No npm dependency graph, provider, major Node version or historical receipt
was changed.

The official [22.23.4 LTS release](https://nodejs.org/en/blog/release/v22.23.4)
was published on **21 October 2026** as a security release. Its
[security advisory](https://nodejs.org/en/blog/vulnerability/october-2026-security-releases)
identifies affected Node 22.x issues including high-severity Brotli decoder
memory corruption (CVE-2026-111497), DNS NUL handling (CVE-2026-105482) and TLS
identity validation fixes. The Brotli issue can permit code execution when an
attacker supplies compressed data that the application decodes. No exploit
against this application was attempted or demonstrated.

This is a production runtime correction: the fresh host's talky-dashboard
service runs /usr/local/bin/node for Next.js. Admin is served as static files
by nginx. The npm and Python package audits do not assess this separately
downloaded Node binary; their passing results did not dispose of this finding.
This review does not establish the Node version on managed Vercel hosting.

Preparation still selects the exact Linux x64 archive checksum from official
SHASUMS256.txt metadata; installation still checks the downloaded archive.
The receipt URL and CLI help now derive from the version constant. Metadata
regressions reject stale-version, wrong-architecture and duplicate matches.
Regenerate protected inputs for this source; never pair the new version with
an old archive checksum. Linux frontend validation requires this exact reviewed
runtime pin and refuses older or unreviewed versions before reading input files.
External frontend mode still requires no local Node archive or installation.

The earlier install/reboot evidence remains valid for its recorded source and
Node runtime. **Qualification of the changed runtime is pending** until the
new Linux builds and actual VM installation/reboot/update checks complete.
The updater continues to refuse an unqualified host Node-version change.
Its baseline must itself be installed and verified with the patched runtime
before testing a descendant update; no runtime guard is relaxed.

## Bounded verification

On the owned Ubuntu test environment, all 15 preparation tests and all 56
installer tests passed without skips. Both changed workflow YAML files parsed
and diff whitespace checks passed. No frontend build was duplicated locally.

The actual helper fetched the official 22.23.4 metadata and selected the
Linux x64 tar.xz SHA-256
1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce.
This verifies metadata selection, not an archive installation or runtime
qualification; those remain the matching CI/VM acceptance.
