#!/usr/bin/env bash
# Temporary probe, round 2. Removed once the scraper is in place.
set +e
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
echo "=== ISRCTN DOC constraints"
curl -sS -m 60 -L -A "$UA" "https://www.isrctn.com/editorial/retrieveFile/81786542-9920-48a0-8fce-09f8428ab843/37855" -o isrctn_doc.pdf
python3 -m pip install -q pypdf >/dev/null 2>&1
python3 - <<'PY'
from pypdf import PdfReader
t = "\n".join(p.extract_text() or "" for p in PdfReader("isrctn_doc.pdf").pages)
i = t.find("3.2.1.1 trialStatus", 3000)
print(t[i:i+9000])
PY
echo "=== BPOR web developer resources"
curl -sS -m 60 -L -A "$UA" "https://bepartofresearch.nihr.ac.uk/researchers-and-health-and-care-professionals/web-developer-resources" -o dev.html -w "HTTP %{http_code}\n"
python3 - <<'PY'
import re, html
t = open("dev.html", encoding="utf-8", errors="ignore").read()
t = re.sub(r"(?s)<(script|style).*?</\1>", " ", t)
t = html.unescape(re.sub(r"<[^>]+>", " ", t))
t = re.sub(r"\s+", " ", t)
i = t.lower().find("api")
print(t[max(0, i-1500):i+3500])
PY
grep -oE 'https?://[^"<> ]*(api|swagger|developer)[^"<> ]*' dev.html | sort -u | head -20
echo "=== BPOR Results.js endpoints"
curl -sS -m 60 -A "$UA" "https://bepartofresearch.nihr.ac.uk/application/themes/be-part-of-research/dist/js/Results.288f291a.js" -o results.js
grep -oE '"[^"]*(api|/search|Search|azure|https?://)[^"]{0,120}"' results.js | sort -u | head -40
