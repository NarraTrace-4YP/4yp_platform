// Fails if Windmill can't read any script in f/ (used by the GitHub Actions checks).
// It uses the same parser as Windmill itself, from the windmill-cli package.
//   npm install --no-save windmill-cli@<version> && node .github/check_windmill_scripts.mjs
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const parserDir = "node_modules/windmill-parser-wasm-py";
const parser = await import(pathToFileURL(path.resolve(parserDir, "windmill_parser_wasm.js")).href);
parser.initSync({ module: fs.readFileSync(path.join(parserDir, "windmill_parser_wasm_bg.wasm")) });

const scripts = fs
  .readdirSync("f", { recursive: true })
  .filter((file) => file.endsWith(".py"))
  .map((file) => path.join("f", file))
  .sort();

let failed = false;
for (const file of scripts) {
  const result = JSON.parse(parser.parse_python(fs.readFileSync(file, "utf8")));
  if (result.type === "Invalid" || result.error) {
    console.log(`::error file=${file}::Windmill can't read this script (check its Python syntax)`);
    failed = true;
  } else if (result.auto_kind === "lib") {
    console.log(`ok   ${file} (shared code, no main)`);
  } else {
    const inputs = result.args.map((arg) => `${arg.name}=${JSON.stringify(arg.default)}`);
    console.log(`ok   ${file} (inputs: ${inputs.join(", ") || "none"})`);
  }
}
console.log(`Checked ${scripts.length} script(s).`);
process.exit(failed ? 1 : 0);
