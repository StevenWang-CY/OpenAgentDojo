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
  env: {
    NEXT_PUBLIC_APP_ENV: process.env.NEXT_PUBLIC_APP_ENV ?? "development",
  },
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
