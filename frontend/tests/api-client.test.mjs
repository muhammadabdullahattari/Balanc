import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";

// Exercise the production transport; only the isolated HTTP boundary is substituted.
const source = await readFile(new URL("../src/api/client.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022,
} }).outputText.replace('"openapi-fetch"', JSON.stringify(import.meta.resolve("openapi-fetch")));
const { createApiClient } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);

function browser(t, handler) {
  const previousFetch = globalThis.fetch;
  const previousWindow = globalThis.window;
  globalThis.window = { location: { origin: "https://web.ledgerpilot.test" } };
  globalThis.fetch = handler;
  t.after(() => { globalThis.fetch = previousFetch; globalThis.window = previousWindow; });
}

test("CSRF initializes once, rotates after login, and always includes cookies", async (t) => {
  const calls = [];
  let tokens = 0;
  browser(t, async (request) => {
    calls.push(request);
    return Response.json(request.url.endsWith("/csrf") ? {csrf_token: `token-${++tokens}`} : {});
  });
  const {client} = createApiClient("https://api.ledgerpilot.test");
  await client.POST("/api/v1/auth/login", {body: {email: "synthetic@example.com", password: "synthetic"}});
  await client.POST("/api/v1/auth/logout");
  assert.equal(tokens, 2);
  assert.deepEqual(calls.filter(r => r.method === "POST").map(r => r.headers.get("X-CSRF-Token")),
    ["token-1", "token-2"]);
  assert.ok(calls.every(r => r.credentials === "include"));
});

test("failed financial writes are returned once without automatic retry", async (t) => {
  let writes = 0;
  browser(t, async (request) => {
    if (request.method === "GET") return Response.json({csrf_token: "synthetic"});
    writes++;
    return Response.json({error: {code: "ERR_DEPENDENCY", message: "Unavailable"}}, {status: 503});
  });
  const {client} = createApiClient("https://api.ledgerpilot.test");
  const result = await client.POST("/api/v1/businesses/{business_id}/payments", {
    params: {path: {business_id: "synthetic"}}, body: {},
  });
  assert.equal(writes, 1);
  assert.equal(result.error.error.code, "ERR_DEPENDENCY");
});

test("failed CSRF initialization prevents the write and can be retried explicitly", async (t) => {
  let calls = 0;
  browser(t, async () => { calls++; return new Response(null, {status: 503}); });
  const {client} = createApiClient("https://api.ledgerpilot.test");
  await assert.rejects(client.POST("/api/v1/auth/logout"), /secure request/);
  await assert.rejects(client.POST("/api/v1/auth/logout"), /secure request/);
  assert.equal(calls, 2);
});

test("cross-origin HTTP is rejected", (t) => {
  browser(t, () => { throw new Error("Must not connect"); });
  assert.throws(() => createApiClient("http://untrusted.example"), /HTTPS/);
});
test("expired safe reads share refresh rotation and retry once", async (t) => {
 let refreshed=false, rotations=0;
 browser(t, async request => {
  if(request.url.endsWith('/csrf'))return Response.json({csrf_token:'synthetic'});
  if(request.url.endsWith('/refresh')){rotations++;await new Promise(resolve=>setTimeout(resolve,15));refreshed=true;return Response.json({status:'refreshed'});}
  return refreshed?Response.json({items:[]}):Response.json({error:{message:'Expired'}},{status:401});
 });
 const {client}=createApiClient('https://api.ledgerpilot.test');
 const responses=await Promise.all([client.GET('/api/v1/businesses'),client.GET('/api/v1/auth/me')]);
 assert.equal(rotations,1);assert.ok(responses.every(r=>r.response.ok));
});

 test("read timeouts are bounded without automatic replay", async t=>{
 let reads=0;
 browser(t,request=>new Promise((resolve,reject)=>{reads++;request.signal.addEventListener("abort",()=>reject(request.signal.reason),{once:true});}));
 const {client}=createApiClient("https://api.ledgerpilot.test",20);
 const keepAlive=setTimeout(()=>{},1000);
 try {await assert.rejects(client.GET("/api/v1/businesses"),error=>error.name === "TimeoutError");assert.equal(reads,1);}finally{clearTimeout(keepAlive);}
 });


test("API configuration rejects credentials, path, query and fragment instead of silently dropping them", t => {
  browser(t, () => { throw new Error("Must not connect"); });
  for (const value of ["https://user:password@api.example", "https://api.example/api", "https://api.example?token=test", "https://api.example#test", "file:///tmp/api"]) {
    assert.throws(() => createApiClient(value), /origin without/);
  }
  assert.doesNotThrow(() => createApiClient("https://api.example/"));
});

test("same-origin remote HTTP cannot transmit credentials, while local loopback remains supported", t => {
 browser(t,()=>{throw new Error("Must not connect");});
 globalThis.window.location.origin="http://remote.example";
 assert.throws(()=>createApiClient("http://remote.example"),/HTTPS/);
 globalThis.window.location.origin="http://localhost:5173";
 assert.doesNotThrow(()=>createApiClient("http://localhost:5173"));
});
