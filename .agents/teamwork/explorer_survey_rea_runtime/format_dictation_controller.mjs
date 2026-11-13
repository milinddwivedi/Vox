import fs from 'node:fs'

const code = fs.readFileSync('d:/Milind/Voice Assistant/.agents/teamwork/explorer_survey_rea_runtime/dictation_controller.js', 'utf8')

// Look at module export declaration: n.d(t, { ... })
const exportMatch = code.match(/n\.d\(t,\{([^}]+)\}\)/)
if (exportMatch) {
  console.log('Exported functions in dictation controller:', exportMatch[1])
}

// Pretty print the first 200 lines
const pretty = code
  .replace(/;/g, ';\n')
  .replace(/\{/g, '{\n')
  .replace(/\}/g, '\n}\n')

fs.writeFileSync('d:/Milind/Voice Assistant/.agents/teamwork/explorer_survey_rea_runtime/dictation_controller_pretty.js', pretty)
console.log('Saved dictation_controller_pretty.js. Total lines:', pretty.split('\n').length)
