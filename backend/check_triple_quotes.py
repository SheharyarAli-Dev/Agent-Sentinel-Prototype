import re

with open('app/models/operation.py', 'r', encoding='utf-8') as f:
    content = f.read()

matches = list(re.finditer(r'"""', content))
print(f'Total triple quotes: {len(matches)}')
for i, m in enumerate(matches):
    line_num = content[:m.start()].count('\n') + 1
    print(f'{i}: pos={m.start()}, line={line_num}')

if len(matches) % 2 != 0:
    print("ERROR: Unmatched triple quotes!")
else:
    print("All triple quotes matched!")