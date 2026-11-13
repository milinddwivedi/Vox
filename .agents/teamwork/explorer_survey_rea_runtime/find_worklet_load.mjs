import { readFile } from './asar_reader.mjs'

const hubSrc = readFile('.webpack/renderer/hub/index.js').toString('utf8')

const idx = hubSrc.indexOf('loadWorkletWithRetry')
console.log('loadWorkletWithRetry index:', idx)
if (idx !== -1) {
  console.log(hubSrc.slice(idx - 200, idx + 1000))
}
