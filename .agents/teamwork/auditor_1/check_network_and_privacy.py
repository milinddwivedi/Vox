import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

src_dir = Path('src')
patterns = [
    r'https?://[^\s\"\'\>]+',
    r'\b(requests|urllib|httpx|aiohttp|socket)\b',
    r'\b(telemetry|sentry|posthog|segment|mixpanel|amplitude)\b',
    r'\b(openai|anthropic|cohere|replicate)\b',
    r'api\.openai\.com',
    r'api\.anthropic\.com',
]

findings = []
for p in src_dir.rglob('*.py'):
    content = p.read_text(encoding='utf-8')
    for pattern in patterns:
        for m in re.finditer(pattern, content, re.IGNORECASE):
            line_no = content[:m.start()].count('\n') + 1
            line = content.splitlines()[line_no - 1].strip()
            findings.append((str(p), line_no, m.group(0), line))

print(f'Total findings: {len(findings)}')
for f, l, match, line in findings:
    print(f'{f}:{l} [{match}] {line}')
