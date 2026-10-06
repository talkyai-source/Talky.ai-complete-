# Remaining contact replay import migration — 2026-10-07

`test_call_5dfa4416_regressions.py` still imported removed `_spelled_by_caller` and the retired scripted `Replay` fixture. The bounded before-check failed collection with that missing-helper import. Only this test module changed; no product source or dead helper was restored.

Five obsolete test functions requiring the deleted caller-spelling heuristic, forced email-before-phone script or live speech-judge wiring were retired; the exact list is [recorded](artifacts/contact-test-contract-5dfa4416/retired-assertions.json). Independent pure detector regressions remain, clearly labeled historical utilities. Caller-message assertions now verify that a retired directive cannot alter caller words.

The original call's distinct pending-email/phone-request regression now uses actual `record_contact` and the existing synthetic SQL fixture. A pending email is saved without being confirmed; an incomplete phone request remains a visible caller-owned quote; an unrelated time answer consumes no phone attempt; later email confirmation leaves the phone unresolved. The model owns those argument interpretations, so this is persistence/state qualification, not proof of speech understanding. A synthetic pending display state without caller ownership correctly does not qualify for saving.

The first post-migration run exposed two fixture assumptions (set with null value, and treating an unowned display state as evidence):2failed/7passed. Those were corrected to the existing documented contract. Final result: **9 passed in1.09s**, RuffF and diff checks clean. No source/test change followed that run.

[Commands, hashes and limits](artifacts/contact-test-contract-5dfa4416/checks.json), [collection failure](artifacts/contact-test-contract-5dfa4416/collection-before.txt), [first run](artifacts/contact-test-contract-5dfa4416/first.txt), [final run](artifacts/contact-test-contract-5dfa4416/final.txt). This is one-module evidence; no whole-suite collection, model, provider, database or audio acceptance is claimed.
