import re

with open(r'd:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_ux\wispr_pill_css.txt', 'r', encoding='utf-8') as f:
    raw = f.read()

# Let's find r.locals map:
m = re.search(r'r\.locals=\{([^}]+)\}', raw)
mapping = {}
if m:
    for item in m.group(1).split(','):
        if ':' in item:
            k, v = item.split(':', 1)
            mapping[v.strip('" ')] = k.strip()

print(f"Mapped {len(mapping)} local class names:")
for cls, name in list(mapping.items())[:20]:
    print(f"  .{cls} -> {name}")

# Replace mangled class names with semantic names in the CSS
css_clean = raw
for cls, name in sorted(mapping.items(), key=lambda x: -len(x[0])):
    css_clean = css_clean.replace(cls, f"__{name}__")

# Format CSS rules
rules = re.findall(r'([^\{\}]+)\{([^\{\}]+)\}', css_clean)
print(f"\nExtracted {len(rules)} CSS rules:")
with open(r'd:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_ux\formatted_wispr_rules.txt', 'w', encoding='utf-8') as f:
    for sel, body in rules:
        sel_clean = sel.strip().replace('\n', ' ')
        body_clean = body.strip().replace(';', ';\n    ')
        entry = f"{sel_clean} {{\n    {body_clean}\n}}\n\n"
        f.write(entry)
print("Saved to formatted_wispr_rules.txt!")
