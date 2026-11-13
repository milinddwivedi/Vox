import { readFile } from './asar_reader.mjs'

const mainSrc = readFile('.webpack/main/index.js').toString('utf8')

function searchRegex(regex, limit = 5, contextSize = 300) {
  let match
  let count = 0
  while ((match = regex.exec(mainSrc)) !== null && count < limit) {
    count++
    const start = Math.max(0, match.index - contextSize)
    const end = Math.min(mainSrc.length, match.index + contextSize)
    console.log(`\n--- Match ${count} (index ${match.index}) ---`)
    console.log(mainSrc.slice(start, end).replace(/\r?\n/g, ' '))
  }
}

console.log('=== Helper Service Spawn / Connection ===')
searchRegex(/spawn\(|fork\(|connect\(|WebSocket|namedpipe|pipe/gi, 5, 200)

console.log('=== Helper Module Definition ===')
searchRegex(/Helper service/gi, 5, 400)
