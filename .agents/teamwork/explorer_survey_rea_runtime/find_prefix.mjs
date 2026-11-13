import { readFile } from './asar_reader.mjs'

const mainSrc = readFile('.webpack/main/index.js').toString('utf8')
console.log(mainSrc.slice(4840000, 4845000))
