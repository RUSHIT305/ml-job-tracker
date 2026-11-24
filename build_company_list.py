"""Find company job boards on Greenhouse, Lever, Ashby and Workday, verify them, and add
the working ones to companies.yaml.

Inputs:
  names.txt  - company names (one per line; add * at the end for trading/quant firms)
               and/or career-page URLs (Workday, Lever, Ashby, Greenhouse links).
  arguments  - files (READMEs, job lists) whose links are harvested for company boards.
"""
import glob, os, re, sys, requests, yaml
from concurrent.futures import ThreadPoolExecutor

H = {"User-Agent": "Mozilla/5.0"}
URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{s}/jobs",
    "lever": "https://api.lever.co/v0/postings/{s}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{s}",
}
GH_RE = re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)")
GH_FOR = re.compile(r"greenhouse\.io/[^\s\"')>]*[?&]for=([A-Za-z0-9_-]+)")
LV_RE = re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]+)")
AB_RE = re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)")
WD_RE = re.compile(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Za-z]{2}/)?([A-Za-z0-9_-]+)", re.I)
SKIP = {"embed", "jobs", "api", "v1", "boards"}

tasks, quant = set(), {}


def harvest(text):
    found = set()
    for s in set(GH_RE.findall(text)) | set(GH_FOR.findall(text)):
        if s not in SKIP: found.add(("greenhouse", s))
    for s in set(LV_RE.findall(text)):
        if s not in SKIP: found.add(("lever", s))
    for s in set(AB_RE.findall(text)):
        if s not in SKIP: found.add(("ashby", s))
    for tenant, wd, site in set(WD_RE.findall(text)):
        found.add(("workday", (f"{tenant.lower()}.{wd.lower()}.myworkdayjobs.com", tenant.lower(), site)))
    tasks.update(found)
    return found


def variants(name):
    a = re.sub(r"[^a-z0-9]", "", name)
    b = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
    out = {a, b}
    for suf in ("hq", "inc", "ai", "labs"):
        out.add(a + suf)
    return {v for v in out if v}


for pattern in sys.argv[1:]:
    for path in glob.glob(pattern):
        harvest(open(path, encoding="utf-8", errors="ignore").read())

owners = {}   # names.txt line -> the boards we will test for it

if os.path.exists("names.txt"):
    for line in open("names.txt", encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "." in line and "/" in line:      # a career-page URL
            owners[line] = harvest(line)
            continue
        is_quant = line.endswith("*")
        name = line.rstrip("*").strip().lower()
        mine = set()
        for g in variants(name):
            if is_quant: quant[g] = True
            for p in URLS:                   # guess: test the name on every platform
                mine.add((p, g))
        tasks.update(mine)
        owners[line] = mine


def check(task):
    p, s = task
    try:
        if p == "workday":
            host, tenant, site = s
            r = requests.post(f"https://{host}/wday/cxs/{tenant}/{site}/jobs", headers=H, timeout=15,
                              json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": "intern"})
        else:
            r = requests.get(URLS[p].format(s=s), headers=H, timeout=15)
        return task if r.status_code == 200 else None
    except Exception:
        return None


def key_of(c):
    return (c["platform"], (c["host"], c["tenant"], c["site"]) if c["platform"] == "workday" else c.get("slug"))


cfg = yaml.safe_load(open("companies.yaml")) or {}
cfg["companies"] = cfg.get("companies") or []
have = {key_of(c) for c in cfg["companies"]}
todo = sorted((t for t in tasks if t not in have), key=str)
print(f"{len(tasks)} candidates, {len(todo)} new to test...")

with ThreadPoolExecutor(max_workers=10) as ex:
    good = [t for t in ex.map(check, todo) if t]

for p, s in good:
    if p == "greenhouse":
        e = {"name": s, "platform": p, "slug": s}
    elif p in ("lever", "ashby"):
        e = {"name": f"{s} [{p}]", "platform": p, "slug": s}
    else:
        e = {"name": f"{s[1]} [workday]", "platform": p, "host": s[0], "tenant": s[1], "site": s[2]}
    if p != "workday" and quant.get(s):
        e["any_field"] = True
    cfg["companies"].append(e)

yaml.safe_dump(cfg, open("companies.yaml", "w"), sort_keys=False, default_flow_style=None)

good_set = set(good) | {t for t in tasks if t in have}
missing = [ln for ln, ts in owners.items() if not (ts & good_set)]
open("not_found.txt", "w", encoding="utf-8").write(
    "# Names with no board found on Greenhouse/Lever/Ashby/Workday. Use their own email alerts,\n"
    "# or add their exact career-page URL (or exact board name) to names.txt.\n" + "\n".join(missing) + "\n")
print(f"{len(owners) - len(missing)} of {len(owners)} names/URLs in names.txt are now watched; "
      f"{len(missing)} were not found (listed in not_found.txt).")
by = {}
for p, _ in good:
    by[p] = by.get(p, 0) + 1
print(f"Added {len(good)} working boards {by}. Total companies: {len(cfg['companies'])}")
