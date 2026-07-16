import type { NextConfig } from "next";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Pin Turbopack to this app directory so the repo-root lockfile is not picked
// as the workspace root (that misconfiguration caused 2+ minute compiles).
const appDir = path.dirname(fileURLToPath(import.meta.url));

const nextConfig: NextConfig = {
  experimental: {
    // The UI accepts PDFs up to 25 MB. Leave room for multipart headers so
    // Next.js does not truncate the body before forwarding it to FastAPI.
    proxyClientMaxBodySize: "30mb",
  },
  turbopack: {
    root: appDir,
  },
};

export default nextConfig;
