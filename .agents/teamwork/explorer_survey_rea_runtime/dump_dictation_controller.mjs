import { readFile } from './asar_reader.mjs'
import fs from 'node:fs'

const mainSrc = readFile('.webpack/main/index.js').toString('utf8')

const modMarker = '18866(e,t,n){'
const idx = mainSrc.indexOf(modMarker)
console.log('18866 module index:', idx)
if (idx !== -1) {
  const modChunk = mainSrc.slice(idx, idx + 25000)
  fs.writeFileSync('d:/Milind/Voice Assistant/.agents/teamwork/explorer_survey_rea_runtime/dictation_controller.js', modChunk)
  console.log('Saved dictation_controller.js (bytes:', modChunk.length, ')')
}
