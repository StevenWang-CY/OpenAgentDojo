import { useState } from "react";

import { Dashboard } from "./Dashboard";
import { LoginForm } from "./LoginForm";

export function App() {
  const [user, setUser] = useState<string | null>(null);

  return (
    <main style={{ fontFamily: "system-ui", padding: 24, maxWidth: 720 }}>
      <h1>fullstack-auth-demo</h1>
      {user ? (
        <Dashboard userId={user} onSignOut={() => setUser(null)} />
      ) : (
        <LoginForm onSignedIn={setUser} />
      )}
    </main>
  );
}
