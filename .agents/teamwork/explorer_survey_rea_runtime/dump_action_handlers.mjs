import { readFile } from './asar_reader.mjs'
import fs from 'node:fs'

const mainSrc = readFile('.webpack/main/index.js').toString('utf8')
const start = 5529400
const end = 5545000
const chunk = mainSrc.slice(start, end)

fs.writeFileSync('d:/Milind/Voice Assistant/.agents/teamwork/explorer_survey_rea_runtime/action_handlers.js', chunk)
console.log('Saved action_handlers.js (length:', chunk.length, ')')
