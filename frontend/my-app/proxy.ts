import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

/**
 * Next.js 16 renames Middleware to Proxy (same functionality, new filename).
 * Clerk's `clerkMiddleware` runs unchanged here.
 *
 * Only the "Chat with PDF" experience requires authentication; the marketing
 * homepage and Clerk's own auth routes stay public.
 */
const isProtectedRoute = createRouteMatcher(["/chat(.*)"]);

export default clerkMiddleware(async (auth, request) => {
  if (isProtectedRoute(request)) {
    await auth.protect();
  }
});

export const config = {
  matcher: [
    // Skip Next.js internals and static files, unless found in search params.
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    // Always run for API routes.
    "/(api|trpc)(.*)",
  ],
};
