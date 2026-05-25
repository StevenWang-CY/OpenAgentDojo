import { describe, expect, it } from "vitest";
import {
  SESSION_EXPIRED_MESSAGE,
  errorMessageForCloseCode,
} from "@/components/workspace/Terminal";

/**
 * FE-P2 audit fix — when the supervision WS closes with 1008 (auth policy
 * violation, FastAPI's default for failed `Depends`) or 4401 (custom
 * "token expired" code from `ws/auth.py`), the terminal badge must
 * surface an *actionable* message + a Refresh affordance instead of the
 * generic "Disconnected" chip.
 *
 * The mapping is pulled out into a pure helper so we can pin the policy
 * here without booting xterm + monaco for a one-line decision.
 */
describe("Terminal close-code → message mapping", () => {
  it("maps 1008 (policy violation) to the session-expired copy", () => {
    expect(errorMessageForCloseCode(1008)).toBe(SESSION_EXPIRED_MESSAGE);
  });

  it("maps 4401 (token expired) to the session-expired copy", () => {
    expect(errorMessageForCloseCode(4401)).toBe(SESSION_EXPIRED_MESSAGE);
  });

  it("returns null for transient close codes so the generic badge wins", () => {
    expect(errorMessageForCloseCode(1006)).toBeNull(); // abnormal closure
    expect(errorMessageForCloseCode(1011)).toBeNull(); // server error
    expect(errorMessageForCloseCode(1000)).toBeNull(); // normal closure
    expect(errorMessageForCloseCode(4404)).toBeNull(); // session reaped
  });

  it("uses the friendly copy verbatim (callers may surface it in toast/title)", () => {
    expect(SESSION_EXPIRED_MESSAGE).toMatch(/session expired/i);
    expect(SESSION_EXPIRED_MESSAGE).toMatch(/refresh/i);
  });
});
