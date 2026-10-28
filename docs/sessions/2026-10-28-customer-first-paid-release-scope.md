# Customer dashboard release preparation — 28 October 2026

The user explicitly excluded reseller operations and staff invitations from
the first paid release. They remain future work. This change makes that scope
visible in the reseller workspace and partner routes while retaining the
existing server authentication, role gates and cosmetic branding preview.
Tenant administration, nested agent settings, reseller billing, overview and
analytics routes show an unavailable notice instead of browser-only mutations,
invented paid invoices or operational totals. This does not implement those
excluded capabilities or close the wider production-readiness plan.

This isolated branch starts at `origin/main`
`b8f7488fefa75928acca158bf3d27dcfbdaa9830`. Its 12 changed `Talk-Leee` paths are
copied exactly from reviewed source
`9c06d8c6510b288b190a20a6a17354259c8bb6b6`: the containment implementation and
tests, plus the existing lockfile update from `qs` 6.16.0 to 6.16.1.
Backend, database, installer and updater files remain at the main-branch base;
this release does not require new API contracts or perform backend activation.

The focused 11 containment tests passed locally with existing Next 16.3.3
dependencies. The final branch requires its own Linux checks against the
committed Next 16.4.1 lockfile, including the production build and type check.
The throwing test component includes the explicit `never` return type required
by that build. Existing branding-preview and direct-route authorization checks
remain covered.

At preparation time, Vercel project `talkleeai` uses repository directory
`Talk-Leee` and Git production branch `main`. The public `talkleeai.com` alias
was verified on ready deployment `dpl_AvnseM28ovfDuoQuT7BogK4PfpW4`, source
`b8f7488f`. Its production API origin remains
`https://api.talkleeai.com/api/v1`. Review and CI precede merging this branch;
production deployment and domain/source verification are separate release
steps. This preparation record does not claim that the new frontend is live.
