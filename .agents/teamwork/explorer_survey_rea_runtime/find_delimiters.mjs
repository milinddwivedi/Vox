import { readFile } from './asar_reader.mjs'

const mainSrc = readFile('.webpack/main/index.js').toString('utf8')

const idx = mainSrc.indexOf('static escapeMessage')
console.log('escapeMessage index:', idx)
if (idx !== -1) {
  console.log('Context before escapeMessage:')
  console.log(mainSrc.slice(idx - 1000, idx))
}
