import fs from 'node:fs'

const asarPath = 'C:\\Users\\milin\\AppData\\Local\\WisprFlow\\app-1.6.1457\\resources\\app.asar'
const fd = fs.openSync(asarPath, 'r')

const headerSizeBuf = Buffer.alloc(16)
fs.readSync(fd, headerSizeBuf, 0, 16, 0)
const headerSize = headerSizeBuf.readUInt32LE(4)
const jsonSize = headerSizeBuf.readUInt32LE(12)

const jsonBuf = Buffer.alloc(jsonSize)
fs.readSync(fd, jsonBuf, 0, jsonSize, 16)
const header = JSON.parse(jsonBuf.toString('utf8'))

// In asar format:
// headerSize is at byte 4 (uint32)
// The header size includes the 8 bytes of size fields or payload.
// Actually:
// 4 bytes: 4
// 4 bytes: header_size (payload size + 8)
// 4 bytes: header_size (payload size + 4)
// 4 bytes: header_json_size
// Payload starts at offset: 16 + jsonSize (or 4 + 4 + header_size? Let's check!)
console.log('headerSize at offset 4:', headerSizeBuf.readUInt32LE(4))
console.log('headerSize at offset 8:', headerSizeBuf.readUInt32LE(8))
console.log('headerSize at offset 12:', headerSizeBuf.readUInt32LE(12))

// Let's find index.js.LICENSE.txt
const files = JSON.parse(fs.readFileSync('d:/Milind/Voice Assistant/.agents/teamwork/explorer_survey_rea_runtime/asar_file_list.json', 'utf8'))
const licenseFile = files.find(f => f.path.endsWith('index.js.LICENSE.txt'))
console.log('License file info:', licenseFile)

// If unpacked, read from unpacked dir; if not unpacked:
const testOffset = 16 + jsonSize + parseInt(licenseFile.offset, 10)
const buf = Buffer.alloc(licenseFile.size)
fs.readSync(fd, buf, 0, licenseFile.size, testOffset)
console.log('Read at testOffset (' + testOffset + '):')
console.log(buf.toString('utf8').slice(0, 200))
