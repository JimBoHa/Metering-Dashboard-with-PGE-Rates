#!/usr/bin/env python3
"""Reject common accidental runtime artifacts and private addresses before publishing."""
import ipaddress
import re
import subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[1]
files=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=root).decode().split('\0')
errors=[]
for name in filter(None,files):
    p=root/name
    if not p.is_file():continue
    if p.suffix.lower() in {'.sqlite3','.sqlite','.db','.xlsx','.xls','.pdf','.csv','.log','.pem','.key'}:
        errors.append(name+': runtime/binary data is forbidden');continue
    content=p.read_text(errors='replace')
    for match in re.finditer(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])',content):
        try:addr=ipaddress.ip_address(match.group())
        except ValueError:continue
        if addr.is_private and not addr.is_loopback and not addr.is_unspecified:
            errors.append(name+': private network address found')
    if re.search(r'gh[pousr]_[A-Za-z0-9]{20,}|' + '-----' + r'BEGIN .*PRIVATE KEY' + '-----',content):errors.append(name+': credential signature found')
if errors:
    print('\n'.join(sorted(set(errors))))
    raise SystemExit(1)
print('Public source check passed: no private IPs, keys or runtime artifacts.')
