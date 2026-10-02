"""Snapshot Clarity (últimos 3 días = período B) para línea base."""
import json
from dotenv import load_dotenv

load_dotenv()

import clarity

print(json.dumps(clarity.fetch_summary(3), indent=2, ensure_ascii=False))
print(json.dumps(clarity.fetch_pages_with_issues(3)[:15], indent=2, ensure_ascii=False))
