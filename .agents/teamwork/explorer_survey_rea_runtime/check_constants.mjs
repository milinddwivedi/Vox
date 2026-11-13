import fs from 'node:fs'

const code = fs.readFileSync('d:/Milind/Voice Assistant/.agents/teamwork/explorer_survey_rea_runtime/helper_protocol.js', 'utf8')

const matches = code.match(/([a-zA-Z0-9_$]+)=["'][^"']+["']/g)
console.log('String constants found near protocol:', matches?.slice(0, 30))
