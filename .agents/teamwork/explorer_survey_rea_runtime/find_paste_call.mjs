import { readFile } from './asar_reader.mjs'

const mainSrc = readFile('.webpack/main/index.js').toString('utf8')

let idx = 0
while ((idx = mainSrc.indexOf('sendHelperRequest)("PasteText"', idx)) !== -1) {
  console.log(`\nHit sendHelperRequest("PasteText") at ${idx}:`)
  console.log(mainSrc.slice(Math.max(0, idx - 150), Math.min(mainSrc.length, idx + 600)))
  idx += 25
}

// Also search without quotes or with PasteText in object
let idx2 = 0
while ((idx2 = mainSrc.indexOf('PasteText:{', idx2)) !== -1) {
  console.log(`\nHit PasteText:{ at ${idx2}:`)
  console.log(mainSrc.slice(Math.max(0, idx2 - 150), Math.min(mainSrc.length, idx2 + 600)))
  idx2 += 11
}
