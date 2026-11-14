# Local-currency price estimates: scope decision, 14 November 2026

The user explicitly asked for this work during the production readiness feature freeze ("i want the ip based currency visibility").

Approved scope:
- On the public pricing page, a visitor outside the United States sees each price in their own currency first, as an estimate, with the US dollar amount that is charged directly under it.
- The country comes from the hosting platform's `x-vercel-ip-country` header. It is used to pick a currency and is not stored.
- Exchange rates come from the free open endpoint of ExchangeRate-API (`open.er-api.com`), fetched by the website server at most every six hours. The request carries no visitor data. Its terms require the "Rates By Exchange Rate API" link, which the estimate note carries.

What this does not change:
- Every price is still charged in US dollars. The local amount is marked with "≈" as an estimate, and the dollar amount stays on the page beside it as "charged in US dollars".
- A visitor in the United States, or anyone for whom the lookup fails, sees dollars only, as before. The server HTML always carries the dollar price.
- Nothing in checkout, invoices or the signed-in Billing page changes.
- Charging in other currencies (Stripe multi-currency prices or Adaptive Pricing) is a separate decision for the owner once the live Stripe keys exist.

Preview: `/pricing/?country=GB` shows what a visitor from that country sees.

Revision, same day: the first version kept the dollar price as the main price and put the local estimate under it in small text. The owner, viewing the live page from Pakistan, reported that it "is still showing the dollar". The order was swapped: the local amount is now the main price.

This record does not close any work package and does not lift the feature freeze. Other unrelated scope remains frozen.
