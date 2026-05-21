import { useState, type FormEvent } from "react";

interface LoginFormProps {
  onSignedIn: (userId: string) => void;
}

export function LoginForm({ onSignedIn }: LoginFormProps) {
  const [userId, setUserId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch("/login", {
        method: "POST",
        credentials: "include",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ userId }),
      });
      if (!res.ok) {
        throw new Error(`login failed: ${res.status}`);
      }
      const body = (await res.json()) as { userId: string };
      onSignedIn(body.userId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "login failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} aria-label="Sign in">
      <h2>Sign in</h2>
      <label style={{ display: "block", marginBottom: 8 }}>
        User ID
        <input
          type="text"
          value={userId}
          onChange={(e) => setUserId(e.target.value)}
          required
          minLength={1}
          aria-label="User ID"
          style={{ display: "block", marginTop: 4 }}
        />
      </label>
      <button type="submit" disabled={submitting || userId.length === 0}>
        {submitting ? "Signing in…" : "Sign in"}
      </button>
      {error && (
        <p role="alert" style={{ color: "crimson" }}>
          {error}
        </p>
      )}
    </form>
  );
}
