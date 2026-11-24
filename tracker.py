import json, os, re, time, requests, yaml
from concurrent.futures import ThreadPoolExecutor

WANT = re.compile(r"\bintern(s|ship|ships)?\b|new grad|graduate|university|early career|entry.level|junior|residen|fellow|apprentic|pre-?doc|student researcher|co-?op|college grad|early talent|campus|trainee|new hire|\b(sde|swe)[ -]?(i|1)\b|(engineer|developer|scientist|analyst) (i|1)\b|associate (software|data|machine|ai|ml)|2028|2029", re.I)
SKIP = re.compile(r"senior|principal|director|manager|\blead\b|\bsr\b|staff (engineer|scientist|software|research|machine|ml)", re.I)
ML = re.compile(r"machine learning|\bml\b|\bai\b|\bllm\b|deep learning|\bnlp\b|computer vision|data scien|applied scien|research scien|research engineer|forward deployed|\bfde\b|\bfdse\b|deployment strateg|reinforcement learning|\bgen ?ai\b|mlops|cuda|inference|robotics|perception|speech|foundation model|software|\bswe\b|\bsde\b|full.?stack|back.?end|front.?end|data engineer|platform|infrastructure|systems engineer|site reliability|\bsre\b|devops|security engineer|mobile|android|\bios\b|embedded|firmware|quant|trader|trading|data analy|analytics|business intelligence|\bbi\b|generative|prompt engineer|ai engineer|solutions engineer|applied ai", re.I)
INTERNISH = re.compile(r"\bintern(s|ship|ships)?\b|new grad|graduate|co-?op", re.I)
ML_ONLY = os.getenv("ML_ONLY", "1") == "1"
ALL_ROLES = os.getenv("ALL_ROLES") == "1"   # "1" = every non-senior role, not just intern/grad titles

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")
H = {"User-Agent": "Mozilla/5.0"}


def greenhouse(c):
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{c['slug']}/jobs", headers=H, timeout=20)
    r.raise_for_status()
    for j in r.json()["jobs"]:
        yield j["id"], j["title"], j["absolute_url"], (j.get("location") or {}).get("name", "")


def lever(c):
    r = requests.get(f"https://api.lever.co/v0/postings/{c['slug']}?mode=json", headers=H, timeout=20)
    r.raise_for_status()
    for j in r.json():
        yield j["id"], j["text"], j["hostedUrl"], (j.get("categories") or {}).get("location", "")


def ashby(c):
    r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{c['slug']}", headers=H, timeout=20)
    r.raise_for_status()
    for j in r.json()["jobs"]:
        yield j["id"], j["title"], j["jobUrl"], j.get("location", "")


def workday(c):
    url = f"https://{c['host']}/wday/cxs/{c['tenant']}/{c['site']}/jobs"
    for term in c.get("search", ["intern", "graduate", "machine learning"]):
        for offset in range(0, 100, 20):
            r = requests.post(url, json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": term}, headers=H, timeout=20)
            r.raise_for_status()
            posts = r.json().get("jobPostings", [])
            for j in posts:
                yield j["externalPath"], j["title"], f"https://{c['host']}/{c['site']}{j['externalPath']}", j.get("locationsText", "")
            if len(posts) < 20:
                break


def amazon(c):
    for term in c.get("search", ["intern", "university graduate", "machine learning"]):
        r = requests.get("https://www.amazon.jobs/en/search.json",
                         params={"base_query": term, "sort": "recent", "result_limit": 50}, headers=H, timeout=20)
        r.raise_for_status()
        for j in r.json().get("jobs", []):
            yield j.get("id_icims") or j.get("id"), j["title"], "https://www.amazon.jobs" + j["job_path"], j.get("normalized_location", "")


FETCH = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workday": workday, "amazon": amazon}


def send(text):
    if not TOKEN:
        print(text)
        return
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                  json={"chat_id": CHAT, "text": text, "disable_web_page_preview": True}, timeout=20)
    time.sleep(0.5)  # stay under Telegram's per-chat rate limit


def fetch_company(c):
    try:
        return c, list(FETCH[c["platform"]](c)), None
    except Exception as e:
        return c, [], e


def main():
    cfg = yaml.safe_load(open("companies.yaml"))["companies"]
    first_run = not os.path.exists("seen.json")
    seen = set() if first_run else set(json.load(open("seen.json")))
    new, failed, open_all = [], [], {}

    with ThreadPoolExecutor(max_workers=12) as ex:
        results = list(ex.map(fetch_company, cfg))

    for c, jobs, err in results:
        if err:
            failed.append(f"{c['name']}: {err}")
            continue
        everything = ALL_ROLES or c.get("all_roles")
        marker = f"{c['name']}:__seeded{'_all' if everything else ''}__"  # switching modes re-seeds silently
        silent = marker not in seen  # newly added company: record its jobs silently
        seen.add(marker)
        for jid, title, url, loc in jobs:
            if SKIP.search(title) and not INTERNISH.search(title):
                continue
            if not (everything or WANT.search(title)):
                continue
            if ML_ONLY and not c.get("any_field") and not ML.search(title):
                continue
            key = f"{c['name']}:{jid}"
            open_all[key] = (c['name'], title, loc, url)
            if key not in seen:
                seen.add(key)
                if not silent:
                    new.append(f"🚨 {c['name']}: {title}\n📍 {loc}\n{url}")

    json.dump(sorted(seen), open("seen.json", "w"))
    with open("open_roles.md", "w", encoding="utf-8") as f:
        f.write(f"# Currently open matching roles ({len(open_all)})\n\n")
        for name, title, loc, url in sorted(open_all.values(), key=lambda x: (x[0].lower(), x[1].lower())):
            f.write(f"- **{name}** | {title} | {loc} | {url}\n")
    print(f"{len(cfg)} companies checked, {len(failed)} failed, {len(new)} new matches.")
    for f in failed[:25]:
        print("[warn]", f)

    if first_run:
        send(f"✅ Tracker live. Checked {len(cfg) - len(failed)} companies. New matching jobs will ping you here.")
    for m in new[:50]:
        send(m)
    if len(new) > 50:
        send(f"…and {len(new) - 50} more new matches. Check the Actions log.")


if __name__ == "__main__":
    main()
