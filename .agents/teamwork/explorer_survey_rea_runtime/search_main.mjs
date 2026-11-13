import { readFile } from './asar_reader.mjs'

const mainBuf = readFile('.webpack/main/index.js')
const mainSrc = mainBuf.toString('utf8')
console.log('Main source length:', mainSrc.length)

function searchPatterns(patterns) {
  const results = {}
  for (const pat of patterns) {
    const re = typeof pat === 'string' ? new RegExp(pat, 'gi') : pat
    let match
    let count = 0
    const snippets = []
    while ((match = re.exec(mainSrc)) !== null && count < 5) {
      count++
      const start = Math.max(0, match.index - 100)
      const end = Math.min(mainSrc.length, match.index + 200)
      snippets.push(mainSrc.slice(start, end).replace(/\r?\n/g, ' '))
    }
    results[pat.toString()] = { count, snippets }
  }
  return results
}

const queries = [
  'Wispr Flow Helper',
  'SetWindowsHookEx',
  'RegisterHotKey',
  'uiohook',
  'iohook',
  'globalShortcut',
  'SendInput',
  'keybd_event',
  'clipboard',
  'opusEncoder',
  'incrementalOpusEncoder',
  '16000',
  'sampleRate',
  'audioWorklet',
  'getUserMedia',
  'dictationState',
  'pasteText',
  'insertText',
  'simulateKey'
]

const searchResults = searchPatterns(queries)
for (const [k, v] of Object.entries(searchResults)) {
  console.log(`=== Query: ${k} (hits: ${v.count}) ===`)
  for (const s of v.snippets) {
    console.log(`   ... ${s} ...\n`)
  }
}
