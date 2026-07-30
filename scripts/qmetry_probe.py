"""QMetry endpoint probe — round 3.

Hypothesis: QMetry's REST API is hosted at qtmcloud.qmetry.com (or a related
QMetry-owned host), not the customer's atlassian.net. The customer's site only
hosts the embedded iframe. Now follow redirects and use the documented `apiKey`
custom header.
"""

from __future__ import annotations

import json
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ["QMETRY_API_KEY"]
CYCLE_ID = "1ZwYH2ObF7AGZa"
PROJECT_ID = 10022
PROJECT_KEY = "SOUSCLOUD"

# Bases to try
BASES = [
    "https://qtmcloud.qmetry.com",
    "https://api.qmetry.com",
    "https://qtm4j.qmetry.com",
]

# Header schemes to try
HEADER_SETS = [
    {"name": "apiKey custom",   "headers": {"apiKey": API_KEY}},
    {"name": "Authorization",   "headers": {"Authorization": API_KEY}},
    {"name": "Authorization Bearer", "headers": {"Authorization": f"Bearer {API_KEY}"}},
]

# (label, method, path, body)
PROBES = [
    ("v2 root",                   "GET",  "/rest/qtm4j/v2", None),
    ("v2 testcycle by id",        "GET",  f"/rest/qtm4j/v2/testcycle/{CYCLE_ID}", None),
    ("v2 testcycle list (POST)",  "POST", "/rest/qtm4j/v2/testcycle/list",
        {"startAt": 0, "maxResults": 5, "projectId": PROJECT_ID}),
    ("automation v1 root",        "GET",  "/rest/qtm4j/automation/api/v1", None),
    ("automation v1 result/json", "POST", "/rest/qtm4j/automation/api/v1/result/json",
        {"projectKey": PROJECT_KEY, "testCycleId": CYCLE_ID, "results": []}),
    # Alternate API root names that QMetry has shipped over the years
    ("qtm4jcloud root",           "GET",  "/rest/qtm4jcloud", None),
    ("qtm4jcloud v1",             "GET",  "/rest/qtm4jcloud/v1", None),
    ("qtm4jcloud v2 testcycle",   "GET",  f"/rest/qtm4jcloud/v2/testcycle/{CYCLE_ID}", None),
    ("qtm-rest v2",               "GET",  "/rest/qmetry/v2", None),
    # OpenAPI / docs
    ("swagger ui",                "GET",  "/swagger-ui.html", None),
    ("swagger ui v2",             "GET",  "/swagger-ui/index.html", None),
    ("api docs",                  "GET",  "/v3/api-docs", None),
]


def _short(s, n=600):
    return s if len(s) <= n else s[:n] + f"...[+{len(s) - n}]"


def probe(base, header_set, label, method, path, body):
    url = base + path
    headers = header_set["headers"]
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as c:
            if method == "GET":
                r = c.get(url, headers=headers)
            else:
                if body is None:
                    r = c.post(url, headers=headers)
                else:
                    r = c.post(url, headers={**headers, "Content-Type": "application/json"}, json=body)
    except Exception as e:
        return f"  EXC {type(e).__name__}: {e}"

    ct = r.headers.get("content-type", "?")
    body_preview = r.text
    if "json" in ct.lower():
        try:
            body_preview = json.dumps(r.json(), indent=2, ensure_ascii=False)
        except Exception:
            pass
    marker = "**" if r.status_code not in (404,) else "  "
    # Show final URL if redirected
    final = "" if str(r.url) == url else f"  (-> {r.url})"
    return f"{marker} [{r.status_code}] {method:4s} {path}{final}\n    {ct}\n    {_short(body_preview)}"


def main():
    if not API_KEY or API_KEY.startswith("<"):
        print("QMETRY_API_KEY missing or placeholder — aborting.")
        sys.exit(2)

    for base in BASES:
        for hs in HEADER_SETS:
            print(f"\n========== BASE {base}  /  AUTH {hs['name']} ==========")
            for p in PROBES:
                print(probe(base, hs, *p))


if __name__ == "__main__":
    main()
