// The website's stylesheet (docs/style.css) parses as written: braces balance, and every rule's selector sits on
// one line, as the file is written. A selector cut short (".feat>.film,.feat>") runs into the next rule and takes
// it over without any error; this caught one that put the films in the margin and broke the Install steps.
const fs = require("fs");
const css = fs.readFileSync(__dirname + "/../docs/style.css", "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
let depth = 0, sel = "", bad = [];
for (const ch of css) {
  if (ch === "{") { if (depth === 0 && /\S\s*\n\s*\S/.test(sel.trim()) || /[>,+~]$/.test(sel.trim())) bad.push(sel.trim()); depth++; sel = ""; }
  else if (ch === "}") { depth--; if (depth < 0) { console.log("FAIL: a } with no {"); process.exit(1); } sel = ""; }
  else if (ch === ";" && depth === 0) sel = "";
  else sel += ch;
}
if (depth !== 0) { console.log("FAIL: braces don't balance (" + depth + " open)"); process.exit(1); }
if (bad.length) { console.log("FAIL: selector over more than one line, or cut short:\n  " + bad.join("\n  ")); process.exit(1); }
console.log("css ok");
