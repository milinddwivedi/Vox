import fs from 'node:fs'

const code = fs.readFileSync('d:/Milind/Voice Assistant/.agents/teamwork/explorer_survey_rea_runtime/paste_implementation.js', 'utf8')

const pretty = code
  .replace(/;/g, ';\n')
  .replace(/\{/g, '{\n')
  .replace(/\}/g, '\n}\n')

fs.writeFileSync('d:/Milind/Voice Assistant/.agents/teamwork/explorer_survey_rea_runtime/paste_implementation_pretty.js', pretty)
console.log('Saved paste_implementation_pretty.js. Lines:', pretty.split('\n').length)
