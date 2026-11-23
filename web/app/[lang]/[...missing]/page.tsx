import { notFound } from "next/navigation";

// A 404 is not an instant navigation target; skip that validation for this catch-all.
export const instant = false;

// Any URL no other route matches: render the site's own (translated) 404 inside the layout.
export default function Missing() {
  notFound();
}
