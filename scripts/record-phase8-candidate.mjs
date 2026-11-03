import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
const roots = ['backend/src','backend/migrations','backend/tests','frontend/src','frontend/tests','frontend/scripts','deploy','.github/workflows'];
const files = ['scripts/record-phase8-candidate.mjs','frontend/index.html','frontend/tsconfig.json','backend/alembic.ini','backend/pyproject.toml','backend/uv.lock','backend/Dockerfile','backend/Dockerfile.ocr','frontend/package.json','frontend/package-lock.json','frontend/vite.config.ts','frontend/playwright.config.ts','frontend/playwright.integration.config.ts','frontend/vitest.config.ts','frontend/vitest.live.config.ts','docker-compose.yml','docker-compose.auth.yml','docker-compose.documents.yml'];
function walk(p) { for(const e of fs.readdirSync(p,{withFileTypes:true})) { if(['__pycache__','node_modules'].includes(e.name)) continue; const f=path.join(p,e.name).replaceAll('\\','/'); if(e.isDirectory()) walk(f); else if(/\.(py|sql|ts|tsx|mjs|css|html|json|toml|lock|yml|sh|ps1)$/.test(f)) files.push(f); } }
for(const p of roots) walk(p);
const hash = v => createHash('sha256').update(v).digest('hex');
const inputs=[...new Set(files)].sort().map(file=>({file,sha256:hash(fs.readFileSync(file,'utf8').replaceAll('\r\n','\n'))}));
const candidate={profile:'Uncommitted local-pilot workspace candidate; not a deployed release',algorithm:'SHA-256 of sorted path/content-hash JSON; UTF-8 source CRLF normalized to LF',sha256:hash(JSON.stringify(inputs)),inputs};
fs.writeFileSync('docs/contracts/phase-8-candidate.json',JSON.stringify(candidate,null,2)+'\n');
console.log(`Candidate recorded from ${inputs.length} source/config/test inputs`);
