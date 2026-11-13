import { readFile } from './asar_reader.mjs'

const mainSrc = readFile('.webpack/main/index.js').toString('utf8')

// Find enum definition like _W.Idle or _W={
let idx = mainSrc.indexOf('._W={')
if (idx === -1) idx = mainSrc.indexOf('._W=')
if (idx === -1) idx = mainSrc.indexOf('_W.Idle')
console.log('Search for _W:', idx)
if (idx !== -1) {
  console.log(mainSrc.slice(idx - 100, idx + 400))
}
