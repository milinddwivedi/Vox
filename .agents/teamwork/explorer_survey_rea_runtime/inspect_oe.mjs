import fs from 'node:fs'

const code = fs.readFileSync('d:/Milind/Voice Assistant/.agents/teamwork/explorer_survey_rea_runtime/action_handlers.js', 'utf8')
const idx = code.indexOf('oe=')
console.log('oe= index in action_handlers.js:', idx)
if (idx !== -1) {
  console.log(code.slice(idx, idx + 2500))
}
