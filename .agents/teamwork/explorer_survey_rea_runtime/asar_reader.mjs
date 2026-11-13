import fs from 'node:fs'
import path from 'node:path'

const asarPath = 'C:\\Users\\milin\\AppData\\Local\\WisprFlow\\app-1.6.1457\\resources\\app.asar'
const fd = fs.openSync(asarPath, 'r')

const headerSizeBuf = Buffer.alloc(16)
fs.readSync(fd, headerSizeBuf, 0, 16, 0)
const headerSize = headerSizeBuf.readUInt32LE(4)
const jsonSize = headerSizeBuf.readUInt32LE(12)

const jsonBuf = Buffer.alloc(jsonSize)
fs.readSync(fd, jsonBuf, 0, jsonSize, 16)
const header = JSON.parse(jsonBuf.toString('utf8'))
const baseDataOffset = 8 + headerSize

export function readFile(relativePath) {
  const parts = relativePath.split(/[\\/]/)
  let cur = header
  for (const part of parts) {
    if (!cur.files || !cur.files[part]) return null
    cur = cur.files[part]
  }
  if (cur.files) return null // directory
  if (cur.unpacked) {
    const unpackedPath = path.join('C:\\Users\\milin\\AppData\\Local\\WisprFlow\\app-1.6.1457\\resources\\app.asar.unpacked', relativePath)
    return fs.readFileSync(unpackedPath)
  }
  const offset = baseDataOffset + parseInt(cur.offset, 10)
  const buf = Buffer.alloc(cur.size)
  fs.readSync(fd, buf, 0, cur.size, offset)
  return buf
}

// Test with license file
const lic = readFile('.webpack/main/index.js.LICENSE.txt')
console.log('Lic preview:', lic.toString('utf8').slice(0, 100))
