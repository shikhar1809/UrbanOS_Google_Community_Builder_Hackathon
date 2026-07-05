import re

with open('public/index.html', 'r', encoding='utf-8') as f:
    c = f.read()

darks = re.findall(r'#0[Aa]0[Aa]0[Aa]|#111[^1]|#161616|#1[Cc]1[Cc]1[Cc]|#262626|#2[Aa]2[Aa]2[Aa]|#333[^3]|rgba\(0,0,0', c)
unique = list(set(darks))
print('Remaining dark refs:', len(darks))
for d in unique:
    print(' ', repr(d))
