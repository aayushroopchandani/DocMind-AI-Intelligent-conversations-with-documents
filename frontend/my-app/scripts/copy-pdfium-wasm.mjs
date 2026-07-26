// Copies the PDFium WebAssembly binary into `public/` so the data-analysis
// PDF viewer loads it from this origin instead of EmbedPDF's default CDN.
//
// Runs automatically via the `predev` / `prebuild` npm scripts. The output is
// git-ignored: it is a build artifact reproduced from node_modules.

import { copyFile, mkdir, stat } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
const DESTINATION = path.join(process.cwd(), "public", "pdfium", "pdfium.wasm");

async function main() {
  let source;
  try {
    // Exposed as an explicit export subpath by @embedpdf/pdfium.
    source = require.resolve("@embedpdf/pdfium/pdfium.wasm");
  } catch {
    console.warn(
      "[pdfium] @embedpdf/pdfium not installed — skipping wasm copy. " +
        "The PDF viewer will fail to load until dependencies are installed.",
    );
    return;
  }

  const [sourceStat, destinationStat] = await Promise.all([
    stat(source),
    stat(DESTINATION).catch(() => null),
  ]);

  // Skip the 4.6 MB copy when the destination is already current.
  if (
    destinationStat &&
    destinationStat.size === sourceStat.size &&
    destinationStat.mtimeMs >= sourceStat.mtimeMs
  ) {
    return;
  }

  await mkdir(path.dirname(DESTINATION), { recursive: true });
  await copyFile(source, DESTINATION);
  console.log(`[pdfium] Copied pdfium.wasm to public/pdfium/`);
}

await main();
