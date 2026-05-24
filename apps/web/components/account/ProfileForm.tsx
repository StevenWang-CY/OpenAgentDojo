"use client";

/**
 * P0-6 — Profile form.
 *
 * Surfaces the editable + read-only profile fields:
 *   - ``display_name`` (mutable; ``PATCH /me``)
 *   - ``handle`` (read-only; set at signup; design decision in P0_DESIGN §P0-6
 *     "Open decisions" — public profile URL is a stable credential)
 *   - ``email`` (read-only here; the two-step email change lives in
 *     ``EmailChangeForm`` which renders inline when the user opens it)
 *   - ``created_at`` ("joined" — purely informational)
 *
 * The Sign-out-everywhere CTA lives at the bottom and is in scope for this
 * tab rather than the Danger tab — it is reversible (the user can sign back
 * in immediately) so it doesn't carry the destructive-token treatment.
 */

import * as React from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { User } from "@arena/shared-types";
import { ApiError, account } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { SectionLabel } from "./AccountView";
import { EmailChangeForm } from "./EmailChangeForm";
import { SignOutAllButton } from "./SignOutAllButton";

const MAX_NAME_LENGTH = 120;

function formatJoinedDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

export interface ProfileFormProps {
  user: User;
  /** When the account has a pending deletion, every mutating endpoint will
   *  return 403. We disable the form's submit + the change-email + sign-out
   *  CTAs so the user doesn't hit a confusing error — the lock banner above
   *  the tabs already explains why. */
  locked: boolean;
}

export function ProfileForm({ user, locked }: ProfileFormProps) {
  const queryClient = useQueryClient();
  const [displayName, setDisplayName] = React.useState(user.display_name ?? "");
  const [error, setError] = React.useState<string | null>(null);
  const [emailFormOpen, setEmailFormOpen] = React.useState(false);

  // Re-sync local state when the upstream user changes (e.g. the email
  // change confirm rotated the cookie and ``/me`` returned a fresh row).
  React.useEffect(() => {
    setDisplayName(user.display_name ?? "");
  }, [user.display_name]);

  const updateMutation = useMutation({
    mutationFn: (next: { display_name?: string | null }) =>
      account.updateProfile(next),
    onSuccess() {
      toast.success("Profile updated.");
      void queryClient.invalidateQueries({ queryKey: ["me"] });
    },
    onError(err: unknown) {
      const message =
        err instanceof ApiError
          ? err.message || "Couldn't update your profile."
          : "Couldn't update your profile.";
      setError(message);
      toast.error(message);
    },
  });

  const dirty = displayName.trim() !== (user.display_name ?? "").trim();

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (updateMutation.isPending || !dirty || locked) return;
    const trimmed = displayName.trim();
    if (trimmed.length > MAX_NAME_LENGTH) {
      setError(`Display name must be ${MAX_NAME_LENGTH} characters or fewer.`);
      return;
    }
    setError(null);
    // Send ``null`` when the user clears the field so the backend can
    // distinguish "set to empty" from "absent" (the schema treats the
    // empty string as a 422). The backend Pydantic model accepts both
    // ``null`` and a non-empty string.
    updateMutation.mutate({ display_name: trimmed.length > 0 ? trimmed : null });
  }

  return (
    <section aria-labelledby="profile-heading" className="space-y-8">
      <header>
        <SectionLabel>profile</SectionLabel>
        <h2 id="profile-heading" className="mt-1 text-lg font-semibold">
          Profile
        </h2>
        <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">
          Your display name shows up on public submissions and badges.
        </p>
      </header>

      <form
        onSubmit={handleSubmit}
        noValidate
        className="space-y-5"
        aria-describedby={error ? "profile-error" : undefined}
        data-testid="profile-form"
      >
        <div className="grid gap-1.5">
          <Label htmlFor="display-name">Display name</Label>
          <Input
            id="display-name"
            name="display_name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            maxLength={MAX_NAME_LENGTH}
            placeholder="Your name"
            disabled={updateMutation.isPending || locked}
            aria-invalid={error ? true : undefined}
            data-testid="display-name-input"
          />
          <p className="text-xs text-[var(--color-muted-foreground)]">
            Up to {MAX_NAME_LENGTH} characters.
          </p>
        </div>

        <div className="grid gap-1.5">
          <Label htmlFor="handle">Handle</Label>
          <Input
            id="handle"
            value={user.handle ? `@${user.handle}` : "—"}
            readOnly
            disabled
            className="font-mono"
            aria-readonly
            data-testid="handle-readonly"
          />
          <p className="text-xs text-[var(--color-muted-foreground)]">
            Set at signup. Your public profile URL is{" "}
            <code className="font-mono">
              /profile/{user.handle ?? "your-handle"}
            </code>
            .
          </p>
        </div>

        {error ? (
          <p
            id="profile-error"
            role="alert"
            aria-live="polite"
            className="rounded-md border border-[oklch(from_var(--color-danger)_l_c_h/0.5)] bg-[oklch(from_var(--color-danger)_l_c_h/0.08)] px-3 py-2 text-xs text-[var(--color-danger)]"
          >
            {error}
          </p>
        ) : null}

        <div className="flex items-center gap-3">
          <Button
            type="submit"
            disabled={!dirty || updateMutation.isPending || locked}
            data-testid="save-profile"
          >
            {updateMutation.isPending ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : null}
            {updateMutation.isPending ? "Saving…" : "Save changes"}
          </Button>
          {dirty ? (
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setDisplayName(user.display_name ?? "");
                setError(null);
              }}
              disabled={updateMutation.isPending}
            >
              Reset
            </Button>
          ) : null}
        </div>
      </form>

      <div className="border-t border-[var(--color-border)] pt-6">
        <SectionLabel>email</SectionLabel>
        <h3 className="mt-1 text-base font-semibold">Email</h3>
        <div className="mt-3 grid gap-1.5">
          <p className="font-mono text-sm" data-testid="email-current">
            {user.email}
          </p>
          {user.pending_email ? (
            <div
              className="mt-2 rounded-md border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-3 py-2 text-sm"
              role="status"
              data-testid="email-pending"
            >
              <p className="font-medium">
                Pending:{" "}
                <span className="font-mono">{user.pending_email}</span>
              </p>
              <p className="mt-1 text-xs text-[var(--color-muted-foreground)]">
                Confirm the change via the link we emailed. The link expires
                in 30 minutes; you can re-request a new one below.
              </p>
            </div>
          ) : null}
        </div>

        <div className="mt-4">
          {emailFormOpen || user.pending_email ? (
            <EmailChangeForm
              user={user}
              locked={locked}
              onDismiss={() => setEmailFormOpen(false)}
            />
          ) : (
            <Button
              variant="secondary"
              onClick={() => setEmailFormOpen(true)}
              disabled={locked}
              data-testid="open-email-change"
            >
              Change email
            </Button>
          )}
        </div>
      </div>

      <div className="border-t border-[var(--color-border)] pt-6">
        <SectionLabel>session</SectionLabel>
        <h3 className="mt-1 text-base font-semibold">Sign out everywhere</h3>
        <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">
          Invalidates every existing session for your account. You&rsquo;ll
          stay signed in on this device.
        </p>
        <p className="mt-1 text-xs text-[var(--color-muted-foreground)]">
          Joined {formatJoinedDate(user.created_at)}.
        </p>
        <div className="mt-3">
          <SignOutAllButton disabled={locked} />
        </div>
      </div>
    </section>
  );
}
