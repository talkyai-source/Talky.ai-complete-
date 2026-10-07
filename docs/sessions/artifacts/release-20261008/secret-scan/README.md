# Reviewed historical checksum findings

These are value-free classification and proof records for 114 historical evidence hashes. The original private scanner artifact is deliberately excluded. `review-summary.json` records the original runner hash and historical state before committing. This packaged `verify_checksums.py` recomputes each proof from Git objects without needing that private artifact; its bytes therefore differ from the original runner hash. Run it from the repository root. Original scanner-to-fingerprint equivalence was independently reviewed before packaging.

All 114 additions are exact fingerprints. No broad exclusions or credential exemptions were added. The local rescan covers the recorded historical CI range, not a claim about future commits.
