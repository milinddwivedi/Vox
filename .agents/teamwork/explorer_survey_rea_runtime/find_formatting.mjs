import { readFile } from './asar_reader.mjs'

const mainSrc = readFile('.webpack/main/index.js').toString('utf8')

let idx = 0
let hits = 0
while ((idx = mainSrc.indexOf('formattedText', idx)) !== -1 && hits < 8) {
  hits++
  console.log(`\nHit formattedText at ${idx}:`)
  console.log(mainSrc.slice(Math.max(0, idx - 100), Math.min(mainSrc.length, idx + 400)))
  idx += 13
}
