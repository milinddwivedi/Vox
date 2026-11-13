import fs from 'node:fs'

const code = fs.readFileSync('d:/Milind/Voice Assistant/.agents/teamwork/explorer_survey_rea_runtime/dictation_controller.js', 'utf8')

function showFn(name) {
  const re = new RegExp(`const\\s+${name}\\s*=\\s*(\\(|async)`, 'g')
  const match = re.exec(code)
  if (match) {
    console.log(`\n=== Function ${name} ===`)
    console.log(code.slice(match.index, match.index + 2000))
  } else {
    // try function name(
    const re2 = new RegExp(`function\\s+${name}\\s*\\(`, 'g')
    const match2 = re2.exec(code)
    if (match2) {
      console.log(`\n=== Function ${name} ===`)
      console.log(code.slice(match2.index, match2.index + 2000))
    } else {
      console.log(`Could not find ${name}`)
    }
  }
}

showFn('Ge') // start dictation
showFn('Ke') // stop dictation
showFn('Xe') // toggle POPO
