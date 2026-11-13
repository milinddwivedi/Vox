import { readFile } from './asar_reader.mjs'

const mainSrc = readFile('.webpack/main/index.js').toString('utf8')

let idx = 0
let hits = []
while ((idx = mainSrc.indexOf('PasteText', idx)) !== -1) {
  hits.push(idx)
  idx += 9
}

console.log('Total PasteText hits:', hits.length)
for (const hit of hits) {
  const snippet = mainSrc.slice(Math.max(0, hit - 100), Math.min(mainSrc.length, hit + 300))
  // Filter out the JSON schema definitions if possible
  if (!snippet.includes('typ:q(void 0,U("PasteText')) {
    console.log(`\nHit at ${hit}:`)
    console.log(snippet.replace(/\r?\n/g, ' '))
  }
}
