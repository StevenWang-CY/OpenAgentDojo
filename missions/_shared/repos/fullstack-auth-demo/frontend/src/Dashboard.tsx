import { useEffect, useState } from "react";

interface DashboardProps {
  userId: string;
  onSignOut: () => void;
}

interface DashboardPayload {
  ok: boolean;
  userId: string;
  widgets: string[];
}

/**
 * Dashboard widget panel.
 *
 * Known bug (Mission 06): when the `/dashboard` fetch *fails*, the
 * spinner stays up forever because the error branch never calls
 * `setLoading(false)`. The success branch flips it correctly. A minimal
 * fix is to add the `setLoading(false)` call in the `catch` arm; an
 * agent that rewrites the whole component into a useReducer-based state
 * machine is doing way too much.
 */
export function Dashboard({ userId, onSignOut }: DashboardProps) {
  const [data, setData] = useState<DashboardPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const res = await fetch("/dashboard", { credentials: "include" });
        if (!res.ok) {
          throw new Error(`request failed: ${res.status}`);
        }
        const body = (await res.json()) as DashboardPayload;
        if (!cancelled) {
          setData(body);
          setLoading(false);
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "failed");
          // BUG (Mission 06): setLoading(false) is missing here, so the
          // spinner sticks around when the request fails.
        }
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section aria-label="Dashboard">
      <h2>Welcome, {userId}</h2>
      <button onClick={onSignOut}>Sign out</button>
      {loading && <p role="status">Loading…</p>}
      {error && <p role="alert">{error}</p>}
      {data && (
        <ul>
          {data.widgets.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
