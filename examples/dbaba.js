// Node.js 22 or later.
const base = (process.env.DBABA_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
const key = process.env.DBABA_API_KEY;
const query = process.argv[2] || "SELECT * FROM orders WHERE customer_id = 42;";
const database = process.argv[3] || process.env.DBABA_DB || "MySQL";
const isSql = /^\s*(?:select|with|insert|update|delete|replace|merge|create|alter|drop|truncate|explain|show|describe|desc|grant|revoke|call|set|begin|commit|rollback)(?:\s|$)/i.test(query) && !query.trimEnd().endsWith("?");
const text = isSql ? `${database}: ${query}` : query;
const cookies = new Map();

async function post(path, body) {
  const headers = { Authorization: `Bearer ${key}` };
  if (cookies.size) {
    headers.Cookie = Array.from(cookies, ([name, value]) => `${name}=${value}`).join("; ");
  }
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(base + path, {
    method: "POST",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(120000),
  });
  for (const cookie of response.headers.getSetCookie()) {
    const pair = cookie.split(";", 1)[0];
    const separator = pair.indexOf("=");
    if (separator < 0) continue;
    const name = pair.slice(0, separator);
    const value = pair.slice(separator + 1);
    if (/;\s*max-age=0(?:;|$)/i.test(cookie)) cookies.delete(name);
    else cookies.set(name, value);
  }
  if (!response.ok) throw new Error(`HTTP ${response.status}: ${await response.text()}`);
  return response.json();
}

async function main() {
  if (!key) throw new Error("Set DBABA_API_KEY in your environment.");
  await post("/api/new-chat");
  const result = await post("/api/message", { text });
  console.log(result.reply);
}

main().catch(error => {
  console.error(error.message);
  process.exitCode = 1;
});
