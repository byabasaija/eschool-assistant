"""Shrink a saved eSchool page into a test fixture by removing inert scripts, styles and SVGs."""

import re
import sys
from pathlib import Path

source, target = map(Path, sys.argv[1:3])
page = source.read_text(encoding="utf-8")
page = re.sub(r"<(script|style|svg)\b.*?</\1>", "", page, flags=re.DOTALL | re.IGNORECASE)
page = re.sub(r"<!--.*?-->", "", page, flags=re.DOTALL)
page = re.sub(r"AIza[0-9A-Za-z_-]{20,}", "REDACTED", page)  # eSchool's Maps embed key
page = re.sub(r"\n\s*\n+", "\n", page)
target.write_text(page, encoding="utf-8")
print(f"{target}: {len(page) // 1024} KB")
