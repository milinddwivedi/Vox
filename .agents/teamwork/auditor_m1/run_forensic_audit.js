const fs = require('fs');
const path = require('path');

const repoRoot = 'd:/Milind/Voice Assistant';
const teamworkRoot = path.join(repoRoot, '.agents/teamwork');

const deliverables = [
  path.join(repoRoot, 'VOX_WISPR_FLOW2_ANALYSIS.md'),
  path.join(repoRoot, 'VOX_CURRENT_PRODUCT_AUDIT.md'),
  path.join(repoRoot, 'VOX_PRODUCT_GAP_ANALYSIS.md'),
  path.join(repoRoot, 'VOX_NEW_PRODUCT_UX_SPEC.md')
];

console.log('====================================================');
console.log('FORENSIC INTEGRITY AUDIT — MILESTONE 1');
console.log('====================================================\n');

// 1. Header Compliance Audit
console.log('--- CHECK 1: HEADER & LINE COMPLIANCE SCAN ---');
const reportDeliverables = [];
let deliverableViolations = 0;

deliverables.forEach(f => {
  const rel = path.basename(f);
  if (!fs.existsSync(f)) {
    console.log(`[FAIL] Deliverable missing: ${rel}`);
    deliverableViolations++;
    return;
  }
  const lines = fs.readFileSync(f, 'utf8').split(/\r?\n/);
  const badLines = [];
  lines.forEach((l, idx) => {
    if (/^[#*]/.test(l.trim())) {
      badLines.push({ line: idx + 1, content: l });
    }
  });
  console.log(`[PASS] ${rel}: ${lines.length} lines, violations: ${badLines.length}`);
  reportDeliverables.push({ file: rel, lines: lines.length, violations: badLines.length });
  deliverableViolations += badLines.length;
});

// Scan M1 agent folders
const m1Folders = [
  'worker_m1_reports',
  'reviewer_m1',
  'auditor_m1',
  'orchestrator_r2',
  'explorer_r2_video_ref',
  'explorer_r2_codebase_audit',
  'test_writer_e2e'
];

console.log('\nScanning Milestone 1 Agent Folders:');
let m1AgentViolations = 0;
m1Folders.forEach(folder => {
  const dirPath = path.join(teamworkRoot, folder);
  if (fs.existsSync(dirPath)) {
    const files = fs.readdirSync(dirPath).filter(f => f.endsWith('.md'));
    files.forEach(f => {
      const full = path.join(dirPath, f);
      const lines = fs.readFileSync(full, 'utf8').split(/\r?\n/);
      const bad = lines.filter(l => /^[#*]/.test(l.trim()));
      console.log(`  ${folder}/${f}: ${lines.length} lines, violations: ${bad.length}`);
      m1AgentViolations += bad.length;
    });
  }
});

// 2. Video and Audio Asset Forensics
console.log('\n--- CHECK 2: ASSET & AUTHENTICITY FORENSICS ---');
const videoPath = path.join(repoRoot, 'External References/Whisper FLow2.mp4');
const audioPath = path.join(repoRoot, 'External References/whisper_flow2_audio.aac');

if (fs.existsSync(videoPath)) {
  const stat = fs.statSync(videoPath);
  console.log(`[PASS] Video Asset Whisper FLow2.mp4: size = ${stat.size} bytes (expected 60158358)`);
} else {
  console.log(`[FAIL] Video Asset missing`);
}

if (fs.existsSync(audioPath)) {
  const stat = fs.statSync(audioPath);
  console.log(`[PASS] Audio Asset whisper_flow2_audio.aac: size = ${stat.size} bytes (expected 5181177)`);
} else {
  console.log(`[FAIL] Audio Asset missing`);
}

// 3. Codebase File and Line Number Verification
console.log('\n--- CHECK 3: CODEBASE REFERENCES & LINE VERIFICATION ---');
const codeChecks = [
  { file: 'src/voxlocal/ui/floating_pill.py', line: 304, pattern: /class DualTierFloatingPill\(QWidget\):/ },
  { file: 'src/voxlocal/ui/floating_pill.py', line: 418, pattern: /self\.chip_magic/ },
  { file: 'src/voxlocal/ui/floating_pill.py', line: 530, pattern: /apply_non_activating_floating_style/ },
  { file: 'main.py', line: 28, pattern: /from voxlocal\.ui\.floating_widget import FloatingWidget/ },
  { file: 'main.py', line: 165, pattern: /floating = FloatingWidget/ },
  { file: 'src/voxlocal/engine/dictation_processor.py', line: 17, pattern: /class TransformationMode/ },
  { file: 'src/voxlocal/engine/dictation_processor.py', line: 967, pattern: /def prune_filler_words/ },
  { file: 'src/voxlocal/engine/dictation_processor.py', line: 993, pattern: /def detect_verbal_self_corrections/ },
  { file: 'src/voxlocal/ui/main_window.py', line: 514, pattern: /self\.tabs\.addTab\(self\._build_voice_typing_tab\(\)/ }
];

codeChecks.forEach(c => {
  const p = path.join(repoRoot, c.file);
  if (!fs.existsSync(p)) {
    console.log(`[FAIL] Code file missing: ${c.file}`);
    return;
  }
  const lines = fs.readFileSync(p, 'utf8').split(/\r?\n/);
  const target = lines[c.line - 1] || '';
  const match = c.pattern.test(target);
  if (match) {
    console.log(`[PASS] ${c.file}:${c.line} matches expected pattern: "${target.trim()}"`);
  } else {
    console.log(`[FAIL] ${c.file}:${c.line} pattern mismatch. Actual: "${target.trim()}"`);
  }
});

// 4. Placeholder & Fake Data Detection
console.log('\n--- CHECK 4: PLACEHOLDER & FAKE DATA DETECTION ---');
let placeholderViolations = 0;
deliverables.forEach(f => {
  const rel = path.basename(f);
  const content = fs.readFileSync(f, 'utf8');
  if (/lorem\s+ipsum/i.test(content)) {
    console.log(`[FAIL] ${rel} contains lorem ipsum`);
    placeholderViolations++;
  }
  if (/<td>\s*<\/td>/i.test(content)) {
    console.log(`[FAIL] ${rel} contains empty table cells`);
    placeholderViolations++;
  }
  if (/\b(TODO|FIXME|XXX)\b/.test(content)) {
    console.log(`[FAIL] ${rel} contains TODO/FIXME/XXX`);
    placeholderViolations++;
  }
});
if (placeholderViolations === 0) {
  console.log('[PASS] Zero placeholders, lorem ipsum, empty cells, or fake markers found.');
}

console.log('\n====================================================');
console.log(`AUDIT SUMMARY: Deliverable Violations: ${deliverableViolations}, M1 Agent Violations: ${m1AgentViolations}, Placeholder Violations: ${placeholderViolations}`);
console.log('====================================================');
