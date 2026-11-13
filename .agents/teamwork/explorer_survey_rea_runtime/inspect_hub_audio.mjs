import { readFile } from './asar_reader.mjs'

const hubSrc = readFile('.webpack/renderer/hub/index.js').toString('utf8')
console.log('Hub source size:', hubSrc.length)

function searchHub(str) {
  let idx = 0
  while ((idx = hubSrc.indexOf(str, idx)) !== -1) {
    console.log(`\nHit "${str}" at ${idx}:`)
    console.log(hubSrc.slice(Math.max(0, idx - 100), Math.min(hubSrc.length, idx + 400)))
    idx += str.length
  }
}

searchHub('getUserMedia')
searchHub('sampleRate')
searchHub('audioContext')
searchHub('AudioWorklet')
