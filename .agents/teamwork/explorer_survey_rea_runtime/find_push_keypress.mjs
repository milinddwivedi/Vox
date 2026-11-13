import { readFile } from './asar_reader.mjs'

const mainSrc = readFile('.webpack/main/index.js').toString('utf8')

const idx = mainSrc.indexOf('pushKeypressEvent')
console.log('pushKeypressEvent index:', idx)
if (idx !== -1) {
  console.log(mainSrc.slice(idx - 200, idx + 1500))
}
