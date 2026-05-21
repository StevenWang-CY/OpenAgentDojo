import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

function App() {
  return (
    <main style={{ fontFamily: "system-ui", padding: 24 }}>
      <h1>data-api-demo</h1>
      <p>
        Backend lives at <code>http://localhost:8000</code>; this placeholder
        UI exists only so the repo pack mirrors the fullstack one.
      </p>
    </main>
  );
}

const container = document.getElementById("root");
if (!container) throw new Error("root element missing");
createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
