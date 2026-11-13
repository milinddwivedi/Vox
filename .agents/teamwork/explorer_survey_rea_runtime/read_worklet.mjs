import { readFile } from './asar_reader.mjs'

const workletCode = readFile('.webpack/renderer/dist/recorderWorklet.js')
console.log('=== recorderWorklet.js ===')
console.log(workletCode.toString('utf8'))
