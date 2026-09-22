# -*- coding: utf-8 -*-
"""Test script to debug JSON parse failures."""
import json
from pathlib import Path

def _strip_control_chars(s: str) -> str:
    out = []
    in_string = False
    escaped = False
    for c in s:
        if escaped:
            out.append(c)
            escaped = False
            continue
        if c == '\\':
            out.append(c)
            escaped = True
            continue
        if c == '"':
            in_string = not in_string
            out.append(c)
            continue
        if in_string and ord(c) < 32 and c not in '\n\r\t':
            continue
        out.append(c)
    return ''.join(out)

debug = Path('output/巨量云图视频/debug')
for f in sorted(debug.glob('*_raw.txt')):
    raw = f.read_text(encoding='utf-8')
    stripped = raw.strip()
    if stripped.startswith('```'):
        first_nl = stripped.find('\n')
        if first_nl != -1:
            stripped = stripped[first_nl + 1:]
        if stripped.endswith('```'):
            stripped = stripped[:-3].rstrip()

    start = stripped.find('{')
    end = stripped.rfind('}')
    if start == -1 or end == -1:
        print(f'{f.stem}: no JSON braces found')
        continue
    sub = stripped[start:end + 1]
    cleaned = _strip_control_chars(sub)

    try:
        data = json.loads(cleaned)
        shots = len(data.get('shots', []))
        print(f'{f.stem}: OK ({shots} shots)')
    except json.JSONDecodeError as e:
        print(f'{f.stem}: FAIL at pos {e.pos}: {e.msg}')
        # Show hex of chars around error
        ctx = cleaned[max(0,e.pos-30):e.pos+30]
        print(f'  context: {repr(ctx)}')

        # Try with strict=False
        try:
            # Just replace all control chars in strings
            import re
            # Remove all control chars except \n \r \t
            cleaned2 = ''.join(c for c in sub if ord(c) >= 32 or c in '\n\r\t')
            data = json.loads(cleaned2)
            shots = len(data.get('shots', []))
            print(f'  -> brute force OK ({shots} shots)')
        except Exception as e2:
            print(f'  -> brute force also fails: {e2}')
