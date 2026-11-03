import fs from "node:fs";
import path from "node:path";
import ts from "typescript";
const root = path.resolve("src");
const contract = JSON.parse(
  fs.readFileSync("../docs/contracts/openapi.json", "utf8"),
);
const operations = [];
function walk(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== "generated") walk(file);
      continue;
    }
    if (!/\.tsx?$/.test(file)) continue;
    const relative = path.relative(root, file).replaceAll("\\", "/");
    const source = ts.createSourceFile(
      file,
      fs.readFileSync(file, "utf8"),
      ts.ScriptTarget.Latest,
      true,
    );
    function inspect(node) {
      if (ts.isCallExpression(node)) {
        const call = node.expression.getText(source);
        if (call === "fetch" && relative !== "api/client.ts")
          throw Error(`Noncanonical fetch in ${relative}`);
        if (/^(fetch|axios)(\.|$)/.test(call) && relative !== "api/client.ts")
          throw Error(`Noncanonical transport in ${relative}`);
        if (/^api\.(GET|POST|PATCH|DELETE|PUT)$/.test(call)) {
          const argument = node.arguments[0];
          if (!argument || !ts.isStringLiteral(argument))
            throw Error(`Unreviewed dynamic API path in ${relative}`);
          const method = call.split(".")[1];
          if (!contract.paths[argument.text]?.[method.toLowerCase()])
            throw Error(`Missing contract ${method} ${argument.text}`);
          operations.push({ file: relative, method, path: argument.text });
        }
      }
      ts.forEachChild(node, inspect);
    }
    inspect(source);
  }
}
walk(root);
const report = {
  scope: "Static frontend call-site/contract inventory; not runtime acceptance",
  call_sites: operations.length,
  distinct_operations: new Set(operations.map((o) => `${o.method} ${o.path}`))
    .size,
  operations,
};
if (process.argv.includes("--write"))
  fs.writeFileSync(
    "../docs/contracts/phase-7-api-inventory.json",
    JSON.stringify(report, null, 2) + "\n",
  );
else {
  const recorded = JSON.parse(
    fs.readFileSync("../docs/contracts/phase-7-api-inventory.json", "utf8"),
  );
  if (JSON.stringify(recorded) !== JSON.stringify(report))
    throw Error(
      "Frontend integration inventory changed; review and regenerate with --write",
    );
}
console.log(
  `${report.call_sites} frontend call sites / ${report.distinct_operations} operations match OpenAPI and inventory`,
);
