import { readFile } from './asar_reader.mjs'

const mainSrc = readFile('.webpack/main/index.js').toString('utf8')

// Around 5550000 to 5553000
console.log('=== K(e) / processEvent ===')
console.log(mainSrc.slice(5548000, 5552000))
