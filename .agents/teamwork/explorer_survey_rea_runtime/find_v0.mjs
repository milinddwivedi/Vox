import { readFile } from './asar_reader.mjs'

const mainSrc = readFile('.webpack/main/index.js').toString('utf8')

let idx = 0
while ((idx = mainSrc.indexOf('v.$0', idx)) !== -1) {
  console.log(`\nHit at ${idx}:`)
  console.log(mainSrc.slice(Math.max(0, idx - 100), Math.min(mainSrc.length, idx + 400)))
  idx += 4
}
