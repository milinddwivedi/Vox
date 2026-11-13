import fs from 'node:fs'

const helperPath = 'C:\\Users\\milin\\AppData\\Local\\WisprFlow\\app-1.6.1457\\resources\\Release\\Wispr Flow Helper.exe'
const fd = fs.openSync(helperPath, 'r')
const stat = fs.fstatSync(fd)
console.log('Helper size:', stat.size)

// Read file in chunks to search for ASCII/UTF-16 strings
const buf = Buffer.alloc(stat.size)
fs.readSync(fd, buf, 0, stat.size, 0)
const str = buf.toString('latin1')

const targets = [
  'SetWindowsHookEx',
  'RegisterHotKey',
  'SendInput',
  'keybd_event',
  'OpenClipboard',
  'SetClipboardData',
  'GetForegroundWindow',
  'GetGUIThreadInfo',
  'AttachThreadInput',
  'UIAutomation',
  'IUIAutomation',
  'WH_KEYBOARD_LL',
  'WH_KEYBOARD',
  'LowLevelKeyboardProc',
  'GetAsyncKeyState',
  'GetKeyState'
]

console.log('=== String search in Wispr Flow Helper.exe ===')
for (const t of targets) {
  let count = 0
  let idx = 0
  while ((idx = str.indexOf(t, idx)) !== -1) {
    count++
    idx += t.length
  }
  // also try UTF-16LE
  const u16 = Buffer.from(t, 'utf16le').toString('latin1')
  let u16Count = 0
  idx = 0
  while ((idx = str.indexOf(u16, idx)) !== -1) {
    u16Count++
    idx += u16.length
  }
  console.log(`Target: ${t.padEnd(25)} ASCII: ${count}, UTF-16: ${u16Count}`)
}
