import fs from 'node:fs'

const files = JSON.parse(fs.readFileSync('d:/Milind/Voice Assistant/.agents/teamwork/explorer_survey_rea_runtime/asar_file_list.json', 'utf8'))
const matches = files.filter(f => f.path.includes('recorderWorklet') || f.path.includes('Worklet'))
console.log('Worklet files:', matches)
