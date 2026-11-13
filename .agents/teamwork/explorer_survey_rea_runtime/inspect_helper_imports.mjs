import fs from 'node:fs'

const raw = fs.readFileSync('d:/Milind/Voice Assistant/.agents/teamwork/rea_evidence/helper_pe_imports.json', 'utf8').replace(/^\uFEFF/, '')
const peEvidence = JSON.parse(raw)
const imports = peEvidence.normalized_result.imports

for (const imp of imports) {
  console.log(`\n=== DLL: ${imp.name} (${imp.functions.length} functions) ===`)
  const fnNames = imp.functions.map(f => f.name || `ordinal_${f.ordinal}`)
  console.log(fnNames.join(', '))
}
