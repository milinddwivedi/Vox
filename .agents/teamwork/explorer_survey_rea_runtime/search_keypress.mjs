import { readFile } from './asar_reader.mjs'

const mainSrc = readFile('.webpack/main/index.js').toString('utf8')

function searchOccurrences(word) {
  let idx = 0
  let count = 0
  while ((idx = mainSrc.indexOf(word, idx)) !== -1 && count < 5) {
    count++
    console.log(`\n=== Found "${word}" at ${idx} ===`)
    console.log(mainSrc.slice(Math.max(0, idx - 150), Math.min(mainSrc.length, idx + 300)))
    idx += word.length
  }
}

searchOccurrences('KeypressEvent')
