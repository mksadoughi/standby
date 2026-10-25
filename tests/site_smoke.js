// The website's script, against a fake page: the GitHub links follow the page's data-repo, opened as a file
// they stay on the page, it never touches the network, and the install link is the agent guide in the same repository.
// Run by tests/test_site.sh.
const fs = require("fs");
const html = fs.readFileSync(__dirname + "/../docs/index.html", "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
let fails = 0;
function ok(label, cond, detail) {
  console.log("  " + label.padEnd(58) + (cond ? "OK" : "FAIL"));
  if (!cond) { fails++; console.log("     " + JSON.stringify(detail)); }
}
async function page(repoAttr) {
  const nodes = ["repo", "readme", "releases"].map((val) => ({ val, href: "#", getAttribute: (n) => (n === "data-gh" ? val : null) }));
  const doc = {
    documentElement: { getAttribute: () => repoAttr },
    querySelectorAll: (sel) => (sel === "[data-gh]" ? nodes : []),
  };
  const calls = [];
  const fetch = (url) => { calls.push(url); return Promise.resolve({ ok: false, json: () => null }); };
  new Function("document", "fetch", script)(doc, fetch);
  await new Promise((r) => setTimeout(r, 10));
  return { by: Object.fromEntries(nodes.map((n) => [n.val, n.href])), calls };
}
(async () => {
  const repo = (html.match(/data-repo="([^"]*)"/) || [])[1];
  ok("the page names its repository (data-repo)", /^[\w.-]+\/[\w.-]+$/.test(repo || ""), repo);
  let p = await page(repo);
  ok("  GitHub links point at it", p.by.repo === "https://github.com/" + repo &&
     p.by.readme === "https://github.com/" + repo + "#readme" && p.by.releases === "https://github.com/" + repo + "/releases", p.by);
  ok("  no network", p.calls.length === 0, p.calls);
  const link = (html.match(/data-copy-text="([^"]+)"/) || [])[1];
  ok("  the install link is the agent guide in the same repository", link === "https://github.com/" + repo + "/blob/main/AGENT-GUIDE.md", link);
  p = await page("");
  ok("no data-repo: links stay on the page", p.by.repo === "#install" && p.by.readme === "../README.md" && p.calls.length === 0, p);
  console.log("\n" + (fails ? "FAILURES: " + fails : "ALL PASS"));
  process.exit(fails ? 1 : 0);
})();
