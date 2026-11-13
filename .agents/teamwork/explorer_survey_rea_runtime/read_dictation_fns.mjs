import fs from 'node:fs'

const code = fs.readFileSync('d:/Milind/Voice Assistant/.agents/teamwork/explorer_survey_rea_runtime/dictation_controller.js', 'utf8')

console.log('=== Ge (Start Dictation) ===')
console.log(code.slice(6246, 8500))

console.log('=== Ke (Stop Dictation) ===')
console.log(code.slice(10750, 12500))

console.log('=== Xe (POPO Toggle) ===')
console.log(code.slice(12461, 14500))
