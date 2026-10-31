#!/usr/bin/env bash
# Temporary probe: dumps raw responses from each source so the parser can be
# written against real data. Removed once the scraper is in place.
set +e
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
CT="https://clinicaltrials.gov/api/v2/studies"

echo "=== CTGOV COUNT geo circle"
curl -sS -m 60 "$CT?filter.geo=distance(56.8,-4.2,170mi)&filter.overallStatus=RECRUITING,NOT_YET_RECRUITING&countTotal=true&pageSize=1&fields=NCTId"; echo
echo "=== CTGOV COUNT query.locn=Scotland"
curl -sS -m 60 "$CT?query.locn=Scotland&filter.overallStatus=RECRUITING,NOT_YET_RECRUITING&countTotal=true&pageSize=1&fields=NCTId"; echo
echo "=== CTGOV COUNT advanced date filter"
curl -sS -m 60 "$CT?filter.geo=distance(56.8,-4.2,170mi)&filter.overallStatus=RECRUITING,NOT_YET_RECRUITING&countTotal=true&pageSize=1&fields=NCTId&filter.advanced=AREA%5BLastUpdatePostDate%5DRANGE%5B2026-10-24,MAX%5D"; echo
echo "=== CTGOV ONE STUDY (Edinburgh, newest)"
curl -sS -m 60 "$CT?filter.geo=distance(55.95,-3.19,15mi)&filter.overallStatus=RECRUITING&pageSize=2&sort=StudyFirstPostDate:desc" -o ct.json
python3 - <<'PY'
import json
d = json.load(open("ct.json"))
print("keys:", list(d.keys()))
s = d["studies"][0]
print("top keys:", list(s.keys()))
ps = s["protocolSection"]
print("modules:", list(ps.keys()))
for k, v in ps.items():
    if k == "contactsLocationsModule":
        v = dict(v); locs = v.get("locations", [])
        v["locations"] = locs[:2]; v["_n_locations"] = len(locs)
    txt = json.dumps({k: v}, indent=1)
    print(txt[:2500])
PY
echo "=== CTGOV healthyVolunteers advanced filter test"
curl -sS -m 60 "$CT?filter.geo=distance(56.8,-4.2,170mi)&filter.overallStatus=RECRUITING&countTotal=true&pageSize=1&fields=NCTId&filter.advanced=AREA%5BHealthyVolunteers%5Dtrue"; echo

echo "=== ISRCTN API DOC PDF"
curl -sS -m 60 -L -A "$UA" "https://www.isrctn.com/editorial/retrieveFile/81786542-9920-48a0-8fce-09f8428ab843/37855" -o isrctn_doc.pdf -w "HTTP %{http_code} %{content_type}\n"
python3 -m pip install -q pypdf >/dev/null 2>&1
python3 - <<'PY'
try:
    from pypdf import PdfReader
    r = PdfReader("isrctn_doc.pdf")
    t = "\n".join(p.extract_text() or "" for p in r.pages)
    print(t[:14000])
except Exception as e:
    print("pdf error", e)
PY
for q in "recruitmentCountry:Scotland" "Scotland" "recruitmentCountry:Scotland%20AND%20recruitmentStatus:Recruiting" "recruitmentCountry:Scotland%20AND%20overallStatus:Ongoing"; do
  echo "=== ISRCTN q=$q"
  curl -sS -m 60 -A "$UA" "https://www.isrctn.com/api/query/format/default?q=$q&limit=1" -w "\nHTTP %{http_code}\n" | head -c 1500; echo
done
echo "=== ISRCTN FULL RECORD"
curl -sS -m 60 -A "$UA" "https://www.isrctn.com/api/query/format/default?q=recruitmentCountry:Scotland&limit=1" | head -c 9000; echo
echo "=== ISRCTN WHO FORMAT"
curl -sS -m 60 -A "$UA" "https://www.isrctn.com/api/query/format/who?q=recruitmentCountry:Scotland&limit=1" | head -c 4000; echo

echo "=== BPOR SEARCH PAGE"
curl -sS -m 60 -L -A "$UA" "https://bepartofresearch.nihr.ac.uk/results/search-results?query=migraine&location=Edinburgh" -o bpor.html -w "HTTP %{http_code} size %{size_download}\n"
head -c 2500 bpor.html; echo
echo "--- api-ish strings"
grep -oE '(https?:)?//[a-zA-Z0-9./_-]*api[a-zA-Z0-9./_?=&-]*' bpor.html | sort -u | head -30
grep -oE '"/[a-zA-Z0-9/_-]*(api|search)[a-zA-Z0-9/_?=&-]*"' bpor.html | sort -u | head -30
grep -oE 'href="/trial-details/[^"]*"' bpor.html | head -10
grep -oE '<script[^>]*src="[^"]*"' bpor.html | head -20

echo "=== CFP HOME"
curl -sS -m 60 -L -A "$UA" "https://www.callforparticipants.com/" -o cfp.html -w "HTTP %{http_code} size %{size_download}\n"
grep -oE 'href="[^"]*(study|studies|search)[^"]*"' cfp.html | sort -u | head -30
for u in "https://www.callforparticipants.com/sitemap.xml" "https://www.callforparticipants.com/rss" "https://www.callforparticipants.com/feed" "https://www.callforparticipants.com/studies"; do
  echo "=== CFP $u"
  curl -sS -m 60 -L -A "$UA" "$u" -w "\nHTTP %{http_code} %{content_type}\n" | head -c 2500; echo
done
