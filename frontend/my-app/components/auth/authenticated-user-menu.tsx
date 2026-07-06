"use client";

import { UserButton } from "@clerk/nextjs";
import { MessageSquareText } from "lucide-react";

interface AuthenticatedUserMenuProps {
  /** Optional extra menu action linking back into the app. */
  showDashboardLink?: boolean;
}

/**
 * Wraps Clerk's `UserButton` (avatar + account menu) with DocMind styling.
 * Rendered only for signed-in users.
 */
export function AuthenticatedUserMenu({
  showDashboardLink = false,
}: AuthenticatedUserMenuProps) {
  return (
    <UserButton
      appearance={{
        elements: {
          avatarBox: "size-8 rounded-full ring-1 ring-border",
        },
      }}
    >
      {showDashboardLink ? (
        <UserButton.MenuItems>
          <UserButton.Link
            label="Chat with PDF"
            labelIcon={<MessageSquareText className="size-4" />}
            href="/chat"
          />
        </UserButton.MenuItems>
      ) : null}
    </UserButton>
  );
}
