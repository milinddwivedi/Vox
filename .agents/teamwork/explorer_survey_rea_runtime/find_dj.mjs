import { readFile } from './asar_reader.mjs'

const hubSrc = readFile('.webpack/renderer/hub/index.js').toString('utf8')

const idx = hubSrc.indexOf('Dj=')
console.log('Dj= index:', idx)
if (idx !== -1) {
  console.log(hubSrc.slice(idx - 50, idx + 300))
}
