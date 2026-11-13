import fs from 'node:fs'

const code = fs.readFileSync('d:/Milind/Voice Assistant/.agents/teamwork/explorer_survey_rea_runtime/dictation_controller.js', 'utf8')

for (const name of ['Ge', 'Ke', 'Xe']) {
  let idx = 0
  while ((idx = code.indexOf(name, idx)) !== -1) {
    console.log(`\nOccurrence of ${name} at ${idx}:`)
    console.log(code.slice(Math.max(0, idx - 50), Math.min(code.length, idx + 200)))
    idx += name.length
  }
}
