import { readFile } from './asar_reader.mjs'
import fs from 'node:fs'

const mainSrc = readFile('.webpack/main/index.js').toString('utf8')
const start = 4886500
const end = 4892500
const chunk = mainSrc.slice(start, end)

fs.writeFileSync('d:/Milind/Voice Assistant/.agents/teamwork/explorer_survey_rea_runtime/paste_implementation.js', chunk)
console.log('Saved paste_implementation.js (length:', chunk.length, ')')
