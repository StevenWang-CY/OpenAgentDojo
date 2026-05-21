#!/usr/bin/env node
/**
 * Verifies that ``src/api.gen.ts`` is in sync with ``apps/api/openapi.json``.
 *
 * Exits non-zero (and prints a unified diff) when regenerating would change
 * the file. Used both by ``pnpm --filter @arena/shared-types check`` and the
 * ``.github/workflows/contracts.yml`` job.
 */
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { tmpdir } from "node:os";
import { mkdtempSync, writeFileSync } from "node:fs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const pkgRoot = resolve(__dirname, "..");
const openapiPath = resolve(pkgRoot, "../../apps/api/openapi.json");
const checkedInPath = resolve(pkgRoot, "src/api.gen.ts");

function run(cmd, args, opts = {}) {
  return execFileSync(cmd, args, {
    cwd: pkgRoot,
    stdio: ["ignore", "pipe", "pipe"],
    encoding: "utf-8",
    ...opts,
  });
}

const tmp = mkdtempSync(`${tmpdir()}/api-gen-`);
const tmpFile = `${tmp}/api.gen.ts`;

// Generate into a temp file, format it with the repo's prettier config,
// and diff against the checked-in copy.
run("pnpm", ["exec", "openapi-typescript", openapiPath, "-o", tmpFile]);
const prettierConfig = resolve(pkgRoot, "../../.prettierrc.json");
run("pnpm", [
  "exec",
  "prettier",
  "--config",
  prettierConfig,
  "--write",
  tmpFile,
]);

const generated = readFileSync(tmpFile, "utf-8");
const checkedIn = readFileSync(checkedInPath, "utf-8");

if (generated === checkedIn) {
  console.log("ok: api.gen.ts is in sync with apps/api/openapi.json");
  process.exit(0);
}

// Drift detected — write the freshly generated copy to a sibling path so
// devs can `cp` it over, then print a diff for CI logs.
const driftPath = `${tmp}/api.gen.expected.ts`;
writeFileSync(driftPath, generated, "utf-8");
console.error(
  "DRIFT: packages/shared-types/src/api.gen.ts is stale.\n" +
    `Expected file written to ${driftPath}\n` +
    "Run: pnpm --filter @arena/shared-types regen\n"
);
try {
  // GNU diff exit code is 1 when files differ — that's exactly what we want
  // captured but not fatal here, since we're going to exit 1 below anyway.
  const diff = run("diff", ["-u", checkedInPath, driftPath], { stdio: ["ignore", "pipe", "pipe"] });
  if (diff) process.stderr.write(diff);
} catch (err) {
  if (err.stdout) process.stderr.write(err.stdout);
}
process.exit(1);
