import json, os, re, time, requests, yaml
from concurrent.futures import ThreadPoolExecutor

WANT = re.compile(r"\bintern(ship)?\b|new grad|graduate|university|early career|entry.level|junior|residen|fellow|apprentic|pre-?doc|student researcher|co-?op|2028|2029", re.I)
SKIP = re.compile(r"senior|principal|director|manager|\blead\b|\bsr\b|staff (engineer|scientist|software|research|machine|ml)", re.I)
ML = re.compile(r"machine learning|\bml\b|\bai\b|\bllm\b|deep learning|\bnlp\b|computer vision|data scien|applied scien|research scien|research engineer|forward deployed|reinforcement learning|\bgen ?ai\b|mlops|cuda|inference|robotics|perception|speech|foundation model", re.I)
ML_ONLY = os.getenv("ML_ONLY", "1") == "1"

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
        r = requests.post(url, json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": term}, headers=H, timeout=20)
        r.raise_for_status()
        for j in r.json().get("jobPostings", []):
            yield j["externalPath"], j["title"], f"https://{c['host']}/{c['site']}{j['externalPath']}", j.get("locationsText", "")


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
    new, failed = [], []

    with ThreadPoolExecutor(max_workers=12) as ex:
        results = list(ex.map(fetch_company, cfg))

    for c, jobs, err in results:
        if err:
            failed.append(f"{c['name']}: {err}")
            continue
        marker = f"{c['name']}:__seeded__"
        silent = marker not in seen  # newly added company: record its jobs silently
        seen.add(marker)
        for jid, title, url, loc in jobs:
            if SKIP.search(title):
                continue
            if not (c.get("all_roles") or WANT.search(title)):
                continue
            if ML_ONLY and not c.get("any_field") and not ML.search(title):
                continue
            key = f"{c['name']}:{jid}"
            if key not in seen:
                seen.add(key)
                if not silent:
                    new.append(f"🚨 {c['name']}: {title}\n📍 {loc}\n{url}")

    json.dump(sorted(seen), open("seen.json", "w"))
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
