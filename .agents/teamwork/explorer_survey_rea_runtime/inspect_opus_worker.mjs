import { readFile } from './asar_reader.mjs'

const workerCode = readFile('.webpack/main/incrementalOpusEncoder.worker.js').toString('utf8')
console.log('Worker code length:', workerCode.length)
console.log('Worker preview:')
console.log(workerCode.slice(0, 500))
// Search for sampleRate or opus
let idx = workerCode.indexOf('sampleRate')
if (idx !== -1) {
  console.log('Found sampleRate in worker:')
  console.log(workerCode.slice(idx - 100, idx + 300))
}
