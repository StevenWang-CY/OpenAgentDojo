// Type shims for third-party packages that ship without (or with incomplete)
// TypeScript definitions. Keep this file *intentional* — every entry should
// have a comment explaining why it's needed.

// gitdiff-parser ships no .d.ts. We use it as a fallback parser only;
// its return shape is loose enough that `unknown` + structural casting
// at the call site is appropriate.
declare module "gitdiff-parser" {
  const parser: { parse(input: string): unknown[] };
  export default parser;
}

// react-diff-view 3.x ships TypeScript, but `style/index.css` is a CSS
// side-effect import that TS doesn't know about by default.
declare module "react-diff-view/style/index.css";

// xterm CSS likewise (loaded dynamically inside the Terminal client component).
declare module "xterm/css/xterm.css";
