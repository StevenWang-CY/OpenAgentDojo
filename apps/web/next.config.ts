import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // typedRoutes is a nice-to-have but conflicts with our many template-literal
  // routes (e.g. `/workspace/${id}`). Re-enable post-MVP with explicit casts.
  // In Next 15 it moved out of `experimental` to a top-level key.
  typedRoutes: false,
  images: {
    remotePatterns: [],
  },
  // No `env` block: Next.js already inlines every `NEXT_PUBLIC_*` variable
  // at build time, so re-declaring NEXT_PUBLIC_APP_ENV here just freezes a
  // stale default at config-load time and shadows the real value.
  // Monaco / xterm are heavy — keep them out of the server bundle.
  transpilePackages: ["monaco-editor"],
  eslint: {
    ignoreDuringBuilds: false,
  },
  typescript: {
    ignoreBuildErrors: false,
  },
};

export default nextConfig;
