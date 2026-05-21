/**
 * Hidden tests for Mission 09 — API Contract Drift.
 *
 * Renders each of the three frontend components with a
 * displayName-only payload (matching the backend wire) and asserts the
 * user name shows up. Also grep's the frontend source for any lingering
 * `user.fullName` reference.
 */
import fs from "node:fs";
import path from "node:path";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Header } from "../../Header";
import { ProfileCard } from "../../ProfileCard";
import { Settings } from "../../Settings";

interface WireUser {
  id: string;
  role: "user" | "admin";
  displayName: string;
  email: string;
}

const FRONTEND_SRC = path.resolve(__dirname, "../..");

const wireUser: WireUser = {
  id: "u-alice",
  role: "user",
  displayName: "Alice Whitfield-Brown",
  email: "alice@example.com",
};

function walkTsxFiles(dir: string, acc: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name === "tests") continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walkTsxFiles(full, acc);
    } else if (entry.isFile() && /\.tsx?$/.test(entry.name)) {
      acc.push(full);
    }
  }
  return acc;
}

describe("Mission 09 — hidden tests", () => {
  it("ProfileCard renders displayName", () => {
    render(<ProfileCard user={wireUser as never} />);
    expect(screen.getByText(/Alice Whitfield-Brown/)).toBeInTheDocument();
  });

  it("Header renders displayName", () => {
    render(<Header user={wireUser as never} onSignOut={() => {}} />);
    expect(screen.getByText(/Alice Whitfield-Brown/)).toBeInTheDocument();
  });

  it("Settings renders displayName", () => {
    render(<Settings user={wireUser as never} onSave={() => {}} />);
    expect(screen.getByText(/Alice Whitfield-Brown/)).toBeInTheDocument();
  });

  it("no frontend file still references user.fullName", () => {
    const offenders: string[] = [];
    for (const file of walkTsxFiles(FRONTEND_SRC)) {
      const body = fs.readFileSync(file, "utf-8");
      if (/user\.fullName\b/.test(body)) {
        offenders.push(path.relative(FRONTEND_SRC, file));
      }
    }
    expect(offenders, `still references user.fullName: ${offenders.join(", ")}`).toEqual([]);
  });
});
