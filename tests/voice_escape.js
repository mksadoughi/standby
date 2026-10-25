// Esc never ends a call: Standby's window comes to the front when Claude Code opens it, so an Esc meant
// for Claude Code (to stop a turn) can land in it, and a call once ended that way. Calls end with End.
// (The plain talk that Esc used to end is gone, and with it the page's Esc for the voice.)
const fs = require("fs"), path = require("path");
const ui = fs.readFileSync(path.join(__dirname, "..", "mac", "ui.py"), "utf8");
const ag = fs.readFileSync(path.join(__dirname, "..", "mac", "agents.js"), "utf8");
let fails = 0; const check = (label, ok, d) => { console.log("  " + label.padEnd(66) + (ok ? "OK" : "FAIL")); if (!ok) { fails++; console.log("    " + JSON.stringify(d)) } };
// every keydown handler that looks at Escape: none of them may end the voice or a call
const handlers = (ui + "\n" + ag).split("\n").filter(l => /addEventListener\("keydown"/.test(l) && /Escape/.test(l));
check("no Escape handler ends the voice or a call", handlers.every(l => !/closeVoice|mxEndCall|call\/end/.test(l)), handlers);
check("  the call's End button and its overlay's close are the ways a call ends", /mxEndCall/.test(ag) && /onclick="closeVoice\(\)"/.test(ui));
console.log(fails ? "FAILURES: " + fails : "ALL PASS"); process.exit(fails ? 1 : 0);
