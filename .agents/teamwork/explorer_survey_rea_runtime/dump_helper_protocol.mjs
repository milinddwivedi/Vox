import { readFile } from './asar_reader.mjs'
import fs from 'node:fs'

const mainSrc = readFile('.webpack/main/index.js').toString('utf8')

// Around 4846000 to 4858000
const start = 4845000
const end = 4858000
const chunk = mainSrc.slice(start, end)

fs.writeFileSync(
  'd:/Milind/Voice Assistant/.agents/teamwork/explorer_survey_rea_runtime/helper_protocol.js',
  chunk
)
console.log('Saved helper_protocol.js (bytes:', chunk.length, ')')
