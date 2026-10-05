# Row-pin source fingerprint correction

The original `row-pin-manifest.json` is preserved unchanged. Its two application-file hashes are not hashes of normalized UTF-8 source, despite the field name. Do not use those historical values as a source-parity assertion.

The manifest-generation command used `Path.read_text()` with no encoding argument, followed by `str.encode()` with no encoding argument. In this Windows Python 3.12 environment, the first defaults to CP1252 (`utf8_mode=0`), while the second defaults to UTF-8. The two application files contain UTF-8 punctuation. The test files are ASCII, so only the application hashes diverged.

`verify-row-pin-hashes.py` reads binary Git blobs from exact source commit `0f69c0f736ec0080ef68a575ccb13e9a6dbe1384` and the preserved manifest from evidence commit `8141266afe40e86b14cd3d56655edd69c6e0b5b9`. For every one of the four source/test files, decoding the committed bytes as CP1252 and encoding them as UTF-8 exactly reproduces the recorded manifest hash. The corrected explicit UTF-8/LF hashes and raw committed-byte hashes are equal and recorded in `row-pin-hash-correction.json`.

This establishes an encoding error in fingerprint calculation, rather than a stale-comment, formatting or behavioral source delta after the 220-case test run. No new runtime test result is claimed. The condition requiring a rerun on changed source therefore did not arise; the script only verifies immutable committed fingerprints. It also verifies that the working copy of the historical manifest still equals its committed content after line-ending normalization.

Reproduce from this repository using the existing interpreter:

```text
python docs/sessions/artifacts/ag06-inbox-account/verify-row-pin-hashes.py
```

Future fingerprints must read raw Git bytes or explicitly decode UTF-8 and normalize CRLF before hashing. The in-progress health-expiry source and tests were not edited or checked out during this audit. Its uncommitted draft manifest was produced using the same historical pattern and must use explicit UTF-8 fingerprints before publication; this artifact corrects only the committed row-pin evidence.
