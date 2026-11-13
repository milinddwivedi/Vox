import { readFile } from './asar_reader.mjs'

const mainSrc = readFile('.webpack/main/index.js').toString('utf8')

const idx = mainSrc.indexOf('["KeypressEvent"')
console.log('["KeypressEvent" index:', idx)
if (idx !== -1) {
  console.log(mainSrc.slice(idx - 100, idx + 1000))
} else {
  // search for KeypressEvent without bracket
  let i = 0
  while ((i = mainSrc.indexOf('KeypressEvent', i)) !== -1) {
    const chunk = mainSrc.slice(Math.max(0, i - 50), Math.min(mainSrc.length, i + 100))
    if (!chunk.includes('{json:') && !chunk.includes('Ye=new Set')) {
      console.log(`Hit at ${i}:`, chunk)
    }
    i += 13
  }
}
