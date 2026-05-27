/**
 * Public repo URL surfaced wherever the FE needs to point at the project's
 * source. Single canonical location so the marketing footer, catalog
 * placeholders, and profile recommendation strip don't drift.
 *
 * The ``NEXT_PUBLIC_REPO_URL`` env var lets a deploy override this without
 * shipping a code change. FE-P4 audit fix — previously the marketing
 * footer hard-coded a personal-fork GitHub URL while every other surface
 * read the env-driven constant from ``ComingSoonCard``; that drift meant
 * a deploy override only landed on three of the four surfaces.
 */
export const PUBLIC_REPO_URL =
  process.env.NEXT_PUBLIC_REPO_URL ??
  "https://github.com/openagentdojo/openagentdojo";
