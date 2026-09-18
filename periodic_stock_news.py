import os
import json
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Đọc từ telebot.env (hoặc .env nếu bạn đổi tên)
load_dotenv("telebot.env")

TELEGRAM_BOT_TOKEN = os.getenv("STOCK_NEWS_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("STOCK_NEWS_CHAT_ID")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "stock_codes.json")
STATE_FILE = os.path.join(BASE_DIR, "stock_news_sent.json")

BASE_URL_TEMPLATE = "https://24hmoney.vn/stock/{code}/news"
TIMEOUT = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    )
}


def load_config() -> dict:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_page(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"Lỗi khi fetch trang {url}: {e}")
        return None


def parse_stock_news(html: str, base_url: str):
    soup = BeautifulSoup(html, "html.parser")
    items = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/news/" not in href and base_url not in href:
            continue

        title = a.get_text(strip=True)
        if not title or len(title) < 5:
            continue

        full_url = urljoin(base_url, href)
        items.append({
            "title": title,
            "url": full_url,
        })

    # Loại trùng
    seen = set()
    unique_items = []
    for it in items:
        key = it["url"]
        if key in seen:
            continue
        seen.add(key)
        unique_items.append(it)

    return unique_items


def load_sent() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception:
        return {}


def save_sent(data: dict):
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=TIMEOUT)
        data = resp.json()
        if not data.get("ok"):
            print(f"Telegram trả về lỗi: {data}")
            return False
        return True
    except Exception as e:
        print(f"Lỗi gửi Telegram: {e}")
        return False


def main():
    config = load_config()
    codes = config.get("codes", [])
    max_news_per_code = config.get("max_news_per_code", 10)

    if not codes:
        print("Không có mã nào trong config.")
        return

    sent_data = load_sent()

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%d/%m/%Y")
    time_str = now.strftime("%H:%M")

    any_news = False

    for code in codes:
        url = BASE_URL_TEMPLATE.format(code=code)
        html_content = fetch_page(url)
        if not html_content:
            print(f"{code}: Không lấy được tin.")
            continue

        articles = parse_stock_news(html_content, url)
        sent_urls = sent_data.get(code, [])
        sent_set = set(sent_urls)

        new_articles = [a for a in articles if a["url"] not in sent_set][:max_news_per_code]

        if not new_articles:
            print(f"{code}: Không có tin mới.")
            continue

        any_news = True

        message_lines = [f"📊 {code} – Bản tin {time_str} – {date_str}\n"]

        for i, art in enumerate(new_articles, start=1):
            message_lines.append(
                f"{i}. {art['title']}\n   {art['url']}"
            )

        full_text = "\n".join(message_lines)

        ok = send_telegram_message(full_text)
        if ok:
            if code not in sent_data:
                sent_data[code] = []
            sent_data[code] = sent_urls + [a["url"] for a in new_articles]

    if not any_news:
        print("Không có tin mới cho bất kỳ mã nào.")
        return

    save_sent(sent_data)
    print("Đã gửi bản tin vào Telegram.")


if __name__ == "__main__":
    main()
