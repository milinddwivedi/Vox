# VoxLocal — Keyboard Shortcuts & Voice Commands Reference

## 1. Global Keyboard Shortcuts

All shortcuts work globally across Windows regardless of which application is currently in focus. You can customize any shortcut in **⚙ Settings, Privacy & Logs -> Global Keyboard Shortcuts**.

| Action | Default Shortcut | Mode / Behavior |
| :--- | :--- | :--- |
| **Start / Stop Voice Typing** | `Ctrl + Shift + Space` | **Toggle-to-Talk:** Press once to start listening, speak naturally, press again to transcribe and insert text at the active cursor position. |
| **Push-to-Talk Voice Typing** | `Ctrl + Win` (Hold) | **Push-to-Talk:** When Voice Typing Mode is set to *Push-to-Talk*, hold the keys down while speaking and release to immediately transcribe and insert text. |
| **Start / Stop Meeting Recording** | `Ctrl + Shift + R` | **Toggle Meeting Recording:** Press once to start recording Microphone + System Audio (WASAPI Loopback), press again to stop, save, and finalize the meeting transcript. |

---

## 2. In-App Navigation Shortcuts

Work seamlessly within the VoxLocal main window for instant, mouse-free productivity:

| Shortcut | Action | Description |
| :--- | :--- | :--- |
| `Ctrl + 1` | Switch to **Voice Typing** | Jumps directly to Tab 0 (Voice Typing & Dictation History). |
| `Ctrl + 2` | Switch to **Meeting Studio** | Jumps directly to Tab 1 (Meeting Studio & Live Monitor). |
| `Ctrl + 3` | Switch to **Reports & Intelligence** | Jumps directly to Tab 2 (Meeting Intelligence & Summaries). |
| `Ctrl + 4` | Switch to **Ask Vox** | Jumps directly to Tab 3 (Semantic Search & Answers). |
| `Ctrl + 5` | Switch to **Folders & Actions** | Jumps directly to Tab 4 (Folder Organization & Action Items). |
| `Ctrl + 6` | Switch to **My Speech & Vocabulary** | Jumps directly to Tab 5 (Custom Vocabulary & Quick Teach). |
| `Ctrl + 7` | Switch to **Settings & Audio** | Jumps directly to Tab 6 (Audio Devices, DSP, Models, Privacy). |
| `Ctrl + ,` | **Instant Settings** | Universal shortcut to immediately open Settings & Audio Devices. |
| `Ctrl + T` | **Toggle Theme** | Instantly switches between Quiet Luxury Obsidian Dark and Atelier Porcelain Light modes. |
| `Ctrl + F` | **Contextual Search** | Focuses the search input in the active tab (Action items, Vocabulary, or Ask Vox). |
| `Escape` | **Clear / Dismiss** | Clears active search filters and dismisses search bar focus. |

### Supported Key Names for Custom Shortcuts
When customizing shortcuts in Settings, combine modifier keys and standard keys with `+`:
- **Modifiers:** `ctrl`, `shift`, `alt`, `win`, `lctrl`, `rctrl`, `lalt`, `ralt`, `lshift`, `rshift`
- **Function Keys:** `f1` through `f24` (e.g., `f8`, `f9`, `ctrl+f9`)
- **Special Keys:** `space`, `tab`, `enter`, `esc`, `insert`, `delete`, `home`, `end`, `pageup`, `pagedown`, `pause`, `` ` ``
- **Alphanumeric:** `a`–`z`, `0`–`9`

---

## 2. Spoken Punctuation Commands (Voice Typing)

When **Spoken Punctuation** is enabled (on by default), you can say any of the following phrases naturally during dictation:

| Spoken Phrase | Inserted Output | Example Speech -> Result |
| :--- | :--- | :--- |
| `"period"` or `"full stop"` | `.` | *"Send the report today period"* -> `Send the report today.` |
| `"comma"` | `,` | *"Hi Alex comma thanks for joining"* -> `Hi Alex, thanks for joining` |
| `"question mark"` | `?` | *"Are we ready to ship question mark"* -> `Are we ready to ship?` |
| `"exclamation mark"` or `"exclamation point"` | `!` | *"Great job team exclamation point"* -> `Great job team!` |
| `"colon"` | `:` | *"Action item colon review PR"* -> `Action item: review PR` |
| `"semicolon"` | `;` | *"First phase complete semicolon second starting"* -> `First phase complete; second starting` |
| `"new line"` or `"next line"` | `\n` (Line break) | *"Best regards comma new line Milind"* -> `Best regards,\nMilind` |
| `"new paragraph"` or `"next paragraph"` | `\n\n` (Paragraph break) | *"First point period new paragraph Second point"* |
| `"open quote"` / `"close quote"` | `"` | *"He said open quote ship it close quote"* -> `He said "ship it"` |
| `"open parenthesis"` / `"close parenthesis"` | `(` / `)` | *"See section 2 open parenthesis page 5 close parenthesis"* |

---

## 3. Hands-Free Spoken Voice Commands

When Hands-Free Voice Typing is active, you don't need to click anything or touch your keyboard. Say any of these control commands:

| Spoken Voice Command | Action Executed | Purpose |
| :--- | :--- | :--- |
| `"stop listening"` or `"stop voice typing"` | Turns OFF voice typing | Safely stops dictation without touching mouse or keyboard. |
| `"delete that"` or `"scratch that"` | Backspaces the last typed phrase | Instantly erases mistakes in chat or documents. |
| `"send message"` or `"press enter"` | Sends the Enter key | Submits chat messages, executes searches, or creates new lines. |
| `"new line"` or `"next line"` | Inserts `\n` (Enter) | Continues dictation on a fresh line. |

---

## 5. Ultra-Compact Vertical Floating Pill Controls

The **Ultra-Compact Vertical Floating Pill** is designed with native Win32 `WS_EX_NOACTIVATE` and `WS_EX_TOPMOST` styling so clicking it **never steals focus** from your active text editor, code IDE, or browser:

- **Top Brand / Speaking Icon (`V`):** Shows live speaking indicator when speech is detected. Click to open the VoxLocal main window.
- **Microphone Icon (`🎙️`):** Toggle Hands-Free Voice Typing on / off.
- **Meeting Recording Icon (`⏺`):** Toggle Meeting Recording on / off (pulses red while actively recording).
- **Reports Icon (`📋`):** Jumps directly to Reports & Meeting Intelligence.
- **Vertical Lines / Dots (`⋮`):** Jumps directly to the Settings & Audio Devices tab.
- **Double-Click anywhere on the pill:** Bring main window to the front.
- **Click & Drag anywhere on the pill:** Reposition the vertical dock anywhere along your screen edges (position is remembered across restarts).
- **Right-Click anywhere on the pill:** Open the quick context menu.
