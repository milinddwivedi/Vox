import fs from 'node:fs'
import path from 'node:path'

const asarPath = 'C:\\Users\\milin\\AppData\\Local\\WisprFlow\\app-1.6.1457\\resources\\app.asar'

const fd = fs.openSync(asarPath, 'r')
const headerSizeBuf = Buffer.alloc(16)
fs.readSync(fd, headerSizeBuf, 0, 16, 0)

const headerSize = headerSizeBuf.readUInt32LE(4)
const jsonSize = headerSizeBuf.readUInt32LE(12)
console.log({ headerSize, jsonSize })

const jsonBuf = Buffer.alloc(jsonSize)
fs.readSync(fd, jsonBuf, 0, jsonSize, 16)
const header = JSON.parse(jsonBuf.toString('utf8'))

const baseOffset = 16 + jsonSize
console.log('Base offset:', baseOffset)

function walk(node, currentPath = '') {
  const result = []
  if (node.files) {
    for (const [name, child] of Object.entries(node.files)) {
      const p = currentPath ? `${currentPath}/${name}` : name
      if (child.files) {
        result.push(...walk(child, p))
      } else {
        result.push({ path: p, size: child.size, offset: child.offset, unpacked: child.unpacked })
      }
    }
  }
  return result
}

const allFiles = walk(header)
console.log(`Total files in asar: ${allFiles.length}`)
const topFiles = allFiles.slice(0, 30)
console.log('Sample files:', topFiles.map(f => f.path))

fs.writeFileSync(
  'd:\\Milind\\Voice Assistant\\.agents\\teamwork\\explorer_survey_rea_runtime\\asar_file_list.json',
  JSON.stringify(allFiles, null, 2)
)
