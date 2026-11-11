/**
 * `/settings/team` moved to `/team`.
 *
 * There was a second Team page here (members, roles, and the invite form).
 * Two pages for one thing meant two lists to keep in step, so the one at
 * `/team` (the sidebar's Team link) now has all of it: the member list and
 * role controls for everyone, and for admins the invite form and the invites
 * still waiting.
 *
 * This redirect stays so links, bookmarks and anything already sent to a
 * colleague keep working.
 */
import { redirect } from "next/navigation";

export default function SettingsTeamRedirectPage() {
    redirect("/team");
}
