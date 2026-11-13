import { readFile } from './asar_reader.mjs'

const hubSrc = readFile('.webpack/renderer/hub/index.js').toString('utf8')

let idx = 0
while ((idx = hubSrc.indexOf('recorder-processor', idx)) !== -1) {
  console.log(`\nHit recorder-processor at ${idx}:`)
  console.log(hubSrc.slice(Math.max(0, idx - 100), Math.min(hubSrc.length, idx + 400)))
  idx += 18
}
