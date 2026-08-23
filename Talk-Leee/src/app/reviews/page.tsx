/**
 * `/reviews` moved to `/admin/reviews`.
 *
 * The management view aggregates every review in the tenant and its backend
 * (`admin_router`, `require_admin_tenant`) has always been admin-only — but the
 * page sat at a top-level route in the main sidebar, offered to every user. A
 * non-admin who clicked it got a 403 dressed up as a broken page.
 *
 * So it lives under the admin panel now, which is where reviews were supposed
 * to surface once submitted. This redirect stays because links, bookmarks and
 * anything already sent to a colleague must keep working — the same
 * preserve-deep-links rule goals.md §4 sets out for moving Security.
 */
import { redirect } from "next/navigation";

export default function ReviewsRedirectPage() {
    redirect("/admin/reviews");
}
