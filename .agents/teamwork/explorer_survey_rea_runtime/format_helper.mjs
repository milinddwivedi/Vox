import fs from 'node:fs'

const code = fs.readFileSync('d:/Milind/Voice Assistant/.agents/teamwork/explorer_survey_rea_runtime/helper_protocol.js', 'utf8')

// Format with basic line breaks around semicolons and braces
const pretty = code
  .replace(/;/g, ';\n')
  .replace(/\{/g, '{\n')
  .replace(/\}/g, '\n}\n')

fs.writeFileSync('d:/Milind/Voice Assistant/.agents/teamwork/explorer_survey_rea_runtime/helper_protocol_pretty.js', pretty)
console.log('Saved pretty helper protocol. Total lines:', pretty.split('\n').length)
