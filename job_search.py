import os
import re
import sys
import json
import time
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
from bs4 import BeautifulSoup

load_dotenv()

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SEEN_JOBS_FILE    = os.path.join(os.path.dirname(__file__), "seen_jobs.json")
SEEN_JOBS_TTL_DAYS = 7
TOP_N = 10

# ── عمليات البحث المخصصة لمجال الـ IT والشبكات والدعم الفني ─────────────────
LINKEDIN_SEARCHES = [
    # مصر
    {"keywords": "IT Technical Support",    "location": "Egypt"},
    {"keywords": "IT Support Engineer",     "location": "Egypt"},
    {"keywords": "Helpdesk Specialist",     "location": "Egypt"},
    {"keywords": "Network Engineer",        "location": "Egypt"},
    {"keywords": "System Administrator",    "location": "Egypt"},
    {"keywords": "Desktop Support",         "location": "Egypt"},
    
    # الخليج
    {"keywords": "IT Support Specialist",   "location": "United Arab Emirates"},
    {"keywords": "Network Support Engineer","location": "United Arab Emirates"},
    {"keywords": "IT Support Engineer",     "location": "Saudi Arabia"},
    {"keywords": "System Administrator",    "location": "Saudi Arabia"},
    {"keywords": "IT Technical Support",    "location": "Qatar"},
    {"keywords": "IT Helpdesk",             "location": "Kuwait"},
    
    # شغل عن بُعد (Remote)
    {"keywords": "IT Support Specialist",   "location": "Worldwide", "remote_only": True},
    {"keywords": "Technical Support",       "location": "Worldwide", "remote_only": True},
    {"keywords": "Helpdesk Tier 1 Tier 2",  "location": "Worldwide", "remote_only": True},
]

# شركات مستهدفة
COMPANY_SEARCHES = [
    {"keywords": "Paymob",    "location": "Egypt"},
    {"keywords": "Instabug",  "location": "Egypt"},
    {"keywords": "Vodafone",  "location": "Egypt"},
    {"keywords": "Raya",      "location": "Egypt"},
    {"keywords": "Careem",    "location": "United Arab Emirates"},
    {"keywords": "Bayzat",    "location": "United Arab Emirates"},
]

# الكلمات المفتاحية لمطابقة العناوين
COMPANY_RELEVANCE_TITLE_WORDS = {
    "it", "support", "technical", "helpdesk", "network", "system", 
    "administrator", "desktop", "infrastructure", "hardware", "cisco",
    "service", "desk", "field", "site"
}

LINKEDIN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── حساب النقط والملاءمة مع الـ CV ─────────────────────────────────────────────

ROLE_SCORES = {
    "it technical support":   50,
    "it support engineer":    48,
    "it support specialist":  45,
    "helpdesk specialist":    42,
    "technical support":      40,
    "network engineer":       38,
    "network administrator":  38,
    "system administrator":   35,
    "desktop support":        30,
    "it administrator":       30,
    "field support":          25,
    "it technician":          25,
}

SKILL_SCORES = {
    "ccna":             30,
    "comptia a+":       30,
    "a+":               15,
    "cisco":            20,
    "networking":       20,
    "active directory": 20,
    "windows server":   18,
    "troubleshooting":  15,
    "tcp/ip":           15,
    "vpn":              12,
    "vlan":             12,
    "dns":              10,
    "dhcp":             10,
    "office 365":       12,
    "hardware":         10,
    "helpdesk":         10,
}

LOCATION_SCORES = {
    "eg": 25, "egypt": 25, "cairo": 25, "giza": 25,
    "ae": 20, "uae": 20, "dubai": 20, "abu dhabi": 20,
    "sa": 18, "saudi": 18, "riyadh": 18,
    "qa": 16, "qatar": 16,
    "kw": 15, "kuwait": 15,
    "worldwide": 15, "global": 15, "remote": 20
}

TARGET_COMPANIES = [
    "vodafone", "orange", "etisalat", "raya", "paymob", "fawry",
    "instabug", "concentrix", "teleperformance", "valu", "btech"
]

LOCATION_CODE_MAP = {
    "united arab emirates": "ae", "uae": "ae", "dubai": "ae",
    "saudi arabia": "sa", "riyadh": "sa",
    "egypt": "eg", "cairo": "eg",
    "qatar": "qa", "kuwait": "kw", "worldwide": "global"
}


def infer_country_code(location: str) -> str:
    loc = location.lower()
    for k, v in LOCATION_CODE_MAP.items():
        if k in loc:
            return v
    return "global"


def score_job(job: dict) -> int:
    title   = (job.get("job_title") or "").lower()
    desc    = (job.get("job_description") or "")[:500].lower()
    city    = (job.get("job_city") or "").lower()
    country = (job.get("job_country") or "").lower()
    company = (job.get("employer_name") or "").lower()
    is_remote = job.get("job_is_remote", False)

    score = 0
    for kw, pts in ROLE_SCORES.items():
        if kw in title:
            score += pts
            break
    skill_pts = sum(pts for kw, pts in SKILL_SCORES.items() if kw in title + " " + desc)
    score += min(skill_pts, 30)
    loc_hay = f"{city} {country}" + (" remote" if is_remote else "")
    for loc, pts in LOCATION_SCORES.items():
        if loc in loc_hay:
            score += pts
            break
    if any(name in company for name in TARGET_COMPANIES):
        score += 10
    if is_remote:
        score += 8
    return score


def score_label(score: int) -> str:
    if score >= 60: return "Excellent match"
    if score >= 45: return "Strong match"
    if score >= 30: return "Good match"
    return "Possible match"


APPLICANT_FETCH_LIMIT = 15


def fetch_applicant_count(url: str) -> int | None:
    if not url:
        return None
    try:
        resp = requests.get(url, headers=LINKEDIN_HEADERS, timeout=10)
        if resp.status_code != 200:
            return None
        m = re.search(r'([\d,]+)\+?\s*(?:applicants|people clicked apply)', resp.text, re.I)
        if m:
            return int(m.group(1).replace(",", ""))
    except requests.RequestException:
        pass
    return None


def applicant_bonus(count: int | None) -> int:
    if count is None:
        return 0
    if count <= 10:
        return 20
    if count <= 25:
        return 14
    if count <= 50:
        return 8
    if count <= 100:
        return 2
    return -8


def enrich_with_competition(jobs: list) -> list:
    ranked = sorted(jobs, key=score_job, reverse=True)
    top, rest = ranked[:APPLICANT_FETCH_LIMIT], ranked[APPLICANT_FETCH_LIMIT:]
    for job in top:
        count = fetch_applicant_count(job.get("job_apply_link"))
        job["_applicants"] = count
        job["_score"] = score_job(job) + applicant_bonus(count)
        time.sleep(0.3)
    for job in rest:
        job["_applicants"] = None
        job["_score"] = score_job(job)
    return sorted(top + rest, key=lambda j: j["_score"], reverse=True)


def parse_card(card, search_location: str) -> dict | None:
    link_tag = card.find("a", class_="base-card__full-link")
    if not link_tag:
        return None
    raw_url = link_tag.get("href", "")
    apply_url = raw_url.split("?")[0] if raw_url else ""
    match = re.search(r"-(\d{8,})$", apply_url)
    job_id = f"li_{match.group(1)}" if match else None
    if not job_id:
        return None

    title_tag   = card.find("h3", class_="base-search-card__title")
    company_tag = card.find("h4", class_="base-search-card__subtitle")
    loc_tag     = card.find("span", class_="job-search-card__location")

    title    = (title_tag.get_text(strip=True)   if title_tag   else "").strip()
    company  = (company_tag.get_text(strip=True) if company_tag else "").strip()
    location = (loc_tag.get_text(strip=True)     if loc_tag     else search_location).strip()

    is_remote = "remote" in location.lower() or "remote" in title.lower()

    return {
        "job_id":        job_id,
        "job_title":     title,
        "employer_name": company,
        "job_city":      location,
        "job_country":   search_location,
        "_search_country": infer_country_code(search_location),
        "job_is_remote": is_remote,
        "job_apply_link": apply_url,
        "job_description": "",
        "apply_options": [{"apply_link": apply_url, "is_direct": False, "publisher": "LinkedIn"}],
    }


def search_linkedin(keywords: str, location: str, remote_only: bool = False) -> list:
    url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    params = {
        "keywords": keywords,
        "f_TPR":    "r259200",
        "start":    0,
    }
    if remote_only:
        params["f_WT"] = "2"
        params["location"] = ""
    else:
        params["location"] = location

    try:
        resp = requests.get(url, headers=LINKEDIN_HEADERS, params=params, timeout=15)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        jobs = []
        for card in soup.find_all("li"):
            job = parse_card(card, location)
            if job:
                jobs.append(job)
        return jobs
    except requests.RequestException:
        return []


def esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_job(rank: int, job: dict) -> str:
    title      = esc(job.get("job_title") or "N/A")
    company    = esc(job.get("employer_name") or "N/A")
    location   = esc(job.get("job_city") or job.get("job_country") or "Unknown")
    is_remote  = job.get("job_is_remote", False)
    is_target  = job.get("_company_match", False)
    score      = job.get("_score", score_job(job))
    applicants = job.get("_applicants")

    work_mode = "Remote" if is_remote else location
    apply_url  = job.get("job_apply_link") or ""
    safe_url   = apply_url.replace("&", "&amp;")
    apply_part = f' | <a href="{safe_url}">Apply on LinkedIn</a>' if safe_url else ""
    badge      = " [TARGET CO.]" if is_target else ""
    
    if applicants is None:
        competition = ""
    elif applicants <= 25:
        competition = f" | {applicants} applicants (low competition)"
    else:
        competition = f" | {applicants} applicants"

    return (
        f"<b>#{rank} {title}</b>{badge}\n"
        f"{company} | {work_mode}\n"
        f"<i>{score_label(score)} ({score} pts)</i>{competition}{apply_part}"
    )


def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    lines = text.split("\n")
    chunks, current = [], ""
    for line in lines:
        candidate = current + line + "\n"
        if len(candidate) > 4000:
            if current:
                chunks.append(current.rstrip())
            current = line + "\n"
        else:
            current = candidate
    if current.strip():
        chunks.append(current.rstrip())
    for chunk in chunks:
        try:
            resp = requests.post(url, json={
                "chat_id":   TELEGRAM_CHAT_ID,
                "text":      chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"Error sending Telegram message: {e}")


def check_config():
    missing = [k for k in ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID")
               if not os.getenv(k) or "your_" in os.getenv(k)]
    if missing:
        print(f"ERROR: Missing values in .env: {', '.join(missing)}")
        sys.exit(1)


def load_seen_jobs() -> dict:
    if not os.path.exists(SEEN_JOBS_FILE):
        return {}
    with open(SEEN_JOBS_FILE, "r") as f:
        data = json.load(f)
    cutoff = (datetime.now() - timedelta(days=SEEN_JOBS_TTL_DAYS)).isoformat()
    return {jid: ts for jid, ts in data.items() if ts >= cutoff}


def save_seen_jobs(seen: dict):
    with open(SEEN_JOBS_FILE, "w") as f:
        json.dump(seen, f)


def main():
    check_config()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting LinkedIn IT job search...")

    seen = load_seen_jobs()
    this_run_ids: set = set()
    general_jobs: list = []
    company_jobs: list = []

    print("--- General IT searches ---")
    for s in LINKEDIN_SEARCHES:
        jobs = search_linkedin(s["keywords"], s["location"], s.get("remote_only", False))
        kept = 0
        for job in jobs:
            job_id = job.get("job_id")
            if not job_id or job_id in seen or job_id in this_run_ids:
                continue
            this_run_ids.add(job_id)
            general_jobs.append(job)
            kept += 1
        print(f"  '{s['keywords']}' / {s['location']} -> {kept} new")

    print("--- Target company searches ---")
    for s in COMPANY_SEARCHES:
        jobs = search_linkedin(s["keywords"], s["location"])
        kept = 0
        for job in jobs:
            job_id = job.get("job_id")
            if not job_id or job_id in seen or job_id in this_run_ids:
                continue
            title_words = set((job.get("job_title") or "").lower().split())
            if not title_words & COMPANY_RELEVANCE_TITLE_WORDS:
                continue
            job["_company_match"] = True
            this_run_ids.add(job_id)
            company_jobs.append(job)
            kept += 1
        print(f"  '{s['keywords']}' / {s['location']} -> {kept} relevant")

    all_new = general_jobs + company_jobs
    if not all_new:
        send_telegram(
            "<b>Daily IT Job Report - " + datetime.now().strftime("%b %d, %Y") + "</b>\n"
            "No new IT/Helpdesk jobs found today. Check back tomorrow!"
        )
    else:
        enriched_general = enrich_with_competition(general_jobs)[:TOP_N]
        enriched_company = enrich_with_competition(company_jobs)[:5]

        report_lines = [
            f"<b>Daily IT Job Report - {datetime.now().strftime('%b %d, %Y')}</b>\n",
            "<b>-- Best Role Matches --</b>"
        ]
        for i, job in enumerate(enriched_general, 1):
            report_lines.append(format_job(i, job))

        if enriched_company:
            report_lines.append("\n<b>-- Target Company Openings --</b>")
            for i, job in enumerate(enriched_company, 1):
                report_lines.append(format_job(i, job))

        send_telegram("\n\n".join(report_lines))

        now_str = datetime.now().isoformat()
        for job in enriched_general + enriched_company:
            seen[job["job_id"]] = now_str
        save_seen_jobs(seen)

    print("Done.")

if __name__ == "__main__":
    main()
