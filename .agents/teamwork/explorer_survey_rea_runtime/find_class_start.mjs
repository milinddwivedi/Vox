import { readFile } from './asar_reader.mjs'

const mainSrc = readFile('.webpack/main/index.js').toString('utf8')
const idx = mainSrc.indexOf('class HelperAPIClient')
console.log('class HelperAPIClient index:', idx)
if (idx === -1) {
  // search for pushData
  const pIdx = mainSrc.indexOf('static pushData')
  console.log('static pushData index:', pIdx)
  console.log(mainSrc.slice(pIdx - 1500, pIdx))
}
