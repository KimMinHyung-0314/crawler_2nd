"""
주기적 크롤링 + 변화 감지 + 카드뉴스 페이지 생성

흐름:  이전 결과 읽기 -> 크롤링 -> 비교(새 글/사라진 글) -> 저장 -> HTML 생성
실행:
  python main.py          # 로컬에서 계속 반복 (INTERVAL_SECONDS 간격)
  python main.py --once   # 한 번만 실행하고 끝
  python main.py --serve  # 크롤링 반복 + 웹서버 동시에 (Railway 배포용)
"""
import html
import json
import os
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# ---- 설정 ----------------------------------------------------------
URL = "https://news.ycombinator.com/"
INTERVAL_SECONDS = int(os.environ.get("INTERVAL_SECONDS", 60 * 10))  # Railway 변수로 바꿀 수 있음
KST = ZoneInfo("Asia/Seoul")            # 서버가 어디 있든 한국 시간으로 표시

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
SITE_DIR = BASE_DIR / "site"
LATEST = DATA_DIR / "latest.json"


# ---- 1단계: 크롤링 -------------------------------------------------
def crawl():
    res = requests.get(URL, headers={"User-Agent": "my-first-crawler"}, timeout=10)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")
    items = []
    for a in soup.select("span.titleline > a"):
        link = urljoin(URL, a["href"])
        items.append({"title": a.get_text(strip=True), "link": link})
    return items


# ---- 2단계: 이전 결과와 비교 (새로 추가된 부분) -------------------
def load_previous():
    """지난번에 저장한 결과를 읽는다. 처음 실행이면 None."""
    if LATEST.exists():
        return json.loads(LATEST.read_text(encoding="utf-8"))
    return None


def compare(prev, items):
    """링크(URL)를 '고유 번호'처럼 써서 이전/현재 목록을 비교한다."""
    if prev is None:  # 첫 실행: 비교 대상이 없음
        return items, []

    prev_links = {i["link"] for i in prev["items"]}
    now_links = {i["link"] for i in items}

    for i in items:
        i["is_new"] = i["link"] not in prev_links          # 이전엔 없었는데 지금 있음
    removed = [i for i in prev["items"] if i["link"] not in now_links]  # 이전엔 있었는데 지금 없음
    return items, removed


# ---- 3단계: 저장 ---------------------------------------------------
def save(items, removed, prev):
    DATA_DIR.mkdir(exist_ok=True)
    data = {
        "crawled_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "prev_crawled_at": prev["crawled_at"] if prev else None,
        "items": items,
        "removed": removed,
    }
    LATEST.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


# ---- 4단계: 카드뉴스 HTML 만들기 ------------------------------------
def card(rank, item):
    e = html.escape
    domain = urlparse(item["link"]).netloc.replace("www.", "")
    new_badge = '<span class="badge">NEW</span>' if item.get("is_new") else ""
    new_class = " new" if item.get("is_new") else ""
    return f"""
    <a class="card{new_class}" href="{e(item['link'])}" target="_blank" rel="noopener">
      <div class="top"><span class="rank">{rank:02d}</span>{new_badge}</div>
      <h2>{e(item['title'])}</h2>
      <div class="domain">{e(domain)}</div>
    </a>"""


def build_site(data):
    SITE_DIR.mkdir(exist_ok=True)
    items, removed = data["items"], data["removed"]
    new_count = sum(1 for i in items if i.get("is_new"))

    if data["prev_crawled_at"]:
        summary = (f'<b>{new_count}</b>개 새 글 · <b>{len(removed)}</b>개 사라짐'
                   f'<small>{data["prev_crawled_at"]} 대비</small>')
    else:
        summary = "첫 수집이에요. 다음 수집부터 변화가 표시됩니다."

    cards = "".join(card(n, i) for n, i in enumerate(items, 1))
    removed_list = "".join(
        f'<li><a href="{html.escape(i["link"])}" target="_blank">{html.escape(i["title"])}</a></li>'
        for i in removed
    ) or "<li>없음</li>"

    page = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>오늘의 해커뉴스 카드</title>
<style>
  :root {{
    --bg:#f4f1ea; --card:#fffdf8; --ink:#1d1b18; --muted:#8a8378;
    --accent:#ff5a1f; --line:#e6e0d4;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#141311; --card:#1f1d1a; --ink:#f2eee6; --muted:#8f887c; --line:#2e2b27; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
         font-family:-apple-system, "Apple SD Gothic Neo", "Noto Sans KR", sans-serif;
         padding: env(safe-area-inset-top) 0 env(safe-area-inset-bottom); }}
  header {{ padding:28px 18px 8px; max-width:960px; margin:0 auto; }}
  header h1 {{ margin:0; font-size:26px; letter-spacing:-0.5px; }}
  .time {{ color:var(--muted); font-size:13px; margin-top:4px; }}
  .summary {{ margin:16px 0 8px; padding:14px 16px; border-radius:14px;
             background:var(--ink); color:var(--bg); font-size:15px; }}
  .summary b {{ color:var(--accent); font-size:18px; }}
  .summary small {{ display:block; opacity:.6; font-size:12px; margin-top:2px; }}
  .filters {{ display:flex; gap:8px; margin:12px 0 4px; }}
  .filters button {{ border:1px solid var(--line); background:var(--card); color:var(--ink);
                    padding:8px 14px; border-radius:999px; font-size:14px; }}
  .filters button.on {{ background:var(--accent); border-color:var(--accent); color:#fff; }}
  main {{ max-width:960px; margin:0 auto; padding:8px 18px 24px;
         display:grid; gap:12px; grid-template-columns:1fr; }}
  @media (min-width:640px) {{ main {{ grid-template-columns:1fr 1fr; }} }}
  .card {{ display:flex; flex-direction:column; gap:10px; min-height:150px;
          background:var(--card); border:1px solid var(--line); border-radius:18px;
          padding:18px; color:inherit; text-decoration:none;
          transition:transform .15s; }}
  .card:active {{ transform:scale(.98); }}
  .card.new {{ border-color:var(--accent); box-shadow:0 0 0 1px var(--accent) inset; }}
  .top {{ display:flex; justify-content:space-between; align-items:center; }}
  .rank {{ font-size:28px; font-weight:800; color:var(--line); }}
  .card.new .rank {{ color:var(--accent); }}
  .badge {{ background:var(--accent); color:#fff; font-size:11px; font-weight:700;
           padding:4px 8px; border-radius:6px; letter-spacing:.5px; }}
  .card h2 {{ margin:0; font-size:18px; line-height:1.4; flex:1; word-break:keep-all; }}
  .domain {{ color:var(--muted); font-size:13px; }}
  body.only-new .card:not(.new) {{ display:none; }}
  details {{ max-width:960px; margin:0 auto 40px; padding:0 18px; color:var(--muted); }}
  details summary {{ cursor:pointer; padding:10px 0; }}
  details a {{ color:var(--muted); }}
  details li {{ margin:6px 0; text-decoration:line-through; }}
</style>
</head>
<body>
<header>
  <h1>Hacker News 카드</h1>
  <div class="time">마지막 수집 {data["crawled_at"]} (KST)</div>
  <div class="summary">{summary}</div>
  <div class="filters">
    <button class="on" onclick="setFilter(false, this)">전체 {len(items)}</button>
    <button onclick="setFilter(true, this)">NEW만 {new_count}</button>
  </div>
</header>
<main>{cards}
</main>
<details>
  <summary>사라진 글 {len(removed)}개 보기</summary>
  <ul>{removed_list}</ul>
</details>
<script>
  function setFilter(onlyNew, btn) {{
    document.body.classList.toggle("only-new", onlyNew);
    document.querySelectorAll(".filters button").forEach(b => b.classList.remove("on"));
    btn.classList.add("on");
  }}
</script>
</body>
</html>"""
    (SITE_DIR / "index.html").write_text(page, encoding="utf-8")


# ---- 전체 작업 한 번 --------------------------------------------
def job():
    try:
        prev = load_previous()
        items = crawl()
        items, removed = compare(prev, items)
        data = save(items, removed, prev)
        build_site(data)
        new_count = sum(1 for i in items if i.get("is_new"))
        print(f"[{data['crawled_at']}] {len(items)}개 수집 · 새 글 {new_count} · 사라짐 {len(removed)}")
    except Exception as e:
        print("에러 발생:", e)
        if "--once" in sys.argv:
            sys.exit(1)


def crawl_forever():
    """정해진 간격마다 job()을 반복. (처음 1회는 서버 시작 전에 이미 실행함)"""
    while True:
        time.sleep(INTERVAL_SECONDS)
        job()


# ---- Railway용: 크롤링 반복 + 웹서버 --------------------------------
def serve():
    job()  # 서버 켜기 전에 한 번 크롤링해서 index.html을 만들어 둔다

    # 크롤링 반복은 '백그라운드 스레드'에서 (웹서버와 동시에 돌아가도록)
    threading.Thread(target=crawl_forever, daemon=True).start()

    # site 폴더를 웹으로 보여주는 서버. Railway가 PORT 환경변수로 포트를 알려준다.
    port = int(os.environ.get("PORT", 8000))
    handler = partial(SimpleHTTPRequestHandler, directory=str(SITE_DIR))
    print(f"웹서버 시작: 포트 {port}")
    ThreadingHTTPServer(("0.0.0.0", port), handler).serve_forever()


if __name__ == "__main__":
    if "--serve" in sys.argv:
        serve()
    elif "--once" in sys.argv:
        job()
    else:
        print(f"{INTERVAL_SECONDS}초마다 크롤링합니다. 멈추려면 Ctrl+C")
        while True:
            job()
            time.sleep(INTERVAL_SECONDS)
