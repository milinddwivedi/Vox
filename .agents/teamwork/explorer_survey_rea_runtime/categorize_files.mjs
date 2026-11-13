import fs from 'node:fs'

const files = JSON.parse(fs.readFileSync('d:/Milind/Voice Assistant/.agents/teamwork/explorer_survey_rea_runtime/asar_file_list.json', 'utf8'))

const categories = {
  webpackMain: [],
  webpackRenderer: [],
  preload: [],
  workers: [],
  other: []
}

for (const f of files) {
  if (f.path.includes('.webpack/main')) categories.webpackMain.push(f)
  else if (f.path.includes('.webpack/renderer')) categories.webpackRenderer.push(f)
  else if (f.path.includes('preload')) categories.preload.push(f)
  else if (f.path.includes('worker')) categories.workers.push(f)
  else categories.other.push(f)
}

console.log('Webpack Main files count:', categories.webpackMain.length)
console.log('Webpack Renderer files count:', categories.webpackRenderer.length)
console.log('Preload files:', categories.preload.map(f => f.path))
console.log('Workers:', categories.workers.map(f => f.path))

// Find JS files in main with sizes
const mainJs = categories.webpackMain
  .filter(f => f.path.endsWith('.js'))
  .sort((a, b) => b.size - a.size)

console.log('Top 15 main JS files by size:')
for (const f of mainJs.slice(0, 15)) {
  console.log(`  ${f.path} (${f.size} bytes)`)
}

// Check renderer directories
const rendererPaths = new Set(categories.webpackRenderer.map(f => f.path.split('/')[2]))
console.log('Renderer entrypoints / directories:', Array.from(rendererPaths))
