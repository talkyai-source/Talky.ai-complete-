# analysis
- Use read-only analysis mode for production code reviews; do not make code changes unless explicitly asked. Confidence: 0.85
- Label every claim as MEASURED, INFERRED, or VENDOR-CLAIMED in audit reports. Confidence: 0.75
- Structure technical audit reports with severity ratings (🔴 critical / 🟠 significant / 🟡 minor), phased execution plans, and explicit gate criteria. Confidence: 0.75
- Perform adversarial correctness reviews that assume fixes may introduce new bugs rather than just verifying original issues. Confidence: 0.70

# workflow
- Do not run git commands; restrict all work to local file changes only. Confidence: 0.80
