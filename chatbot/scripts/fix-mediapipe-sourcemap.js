const fs = require("fs");
const path = require("path");

const pkgDir = path.join(__dirname, "..", "node_modules", "@mediapipe", "tasks-vision");
const sourceMapActual = path.join(pkgDir, "vision_bundle.mjs.map");
const sourceMapExpected = path.join(pkgDir, "vision_bundle_mjs.js.map");

try {
  if (!fs.existsSync(pkgDir)) {
    process.exit(0);
  }

  if (fs.existsSync(sourceMapExpected)) {
    process.exit(0);
  }

  if (fs.existsSync(sourceMapActual)) {
    fs.copyFileSync(sourceMapActual, sourceMapExpected);
    process.exit(0);
  }

  fs.writeFileSync(sourceMapExpected, "{}\n", "utf8");
} catch (err) {
  console.warn("[postinstall] mediapipe source-map fix skipped:", err.message);
}
