import { readFile } from './asar_reader.mjs'
import fs from 'node:fs'

const files = JSON.parse(fs.readFileSync('d:/Milind/Voice Assistant/.agents/teamwork/explorer_survey_rea_runtime/asar_file_list.json', 'utf8'))

console.log('Searching for MediaManager in files...')
const matches = []

for (const f of files) {
  if (f.path.endsWith('.js') && !f.path.endsWith('.LICENSE.txt')) {
    const buf = readFile(f.path)
    if (buf) {
      const str = buf.toString('utf8')
      if (str.includes('MediaManager') || str.includes('dictation:audioStart')) {
        matches.push({ path: f.path, size: f.size })
      }
    }
  }
}

console.log('Matches found:', matches)
