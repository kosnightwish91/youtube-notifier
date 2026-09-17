from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from random import uniform
from zoneinfo import ZoneInfo

import feedparser
import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / "telebot.env")

CHANNEL_ID = "UCAJ9i1NhhlnosAGu7QcBEtw"
UPLOADS_PLAYLIST_ID = "UUAJ9i1NhhlnosAGu7QcBEtw"
RSS_URL = (
    "https://www.youtube.com/feeds/videos.xml"
    f"?playlist_id={UPLOADS_PLAYLIST_ID}"
)

STATE_FILE = BASE_DIR / "youtube_state.json"
LOG_FILE = BASE_DIR / "youtube_notifier.log"
TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0 Safari/537.36"
    ),
    "Accept": "application/atom+xml,application/xml,text/xml,*/*",
}


def write_log(message: str) -> None:
    timestamp = datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    with LOG_FILE.open("a", encoding="utf-8") as file:
        file.write(f"{timestamp} | {message}\n")


def format_time(value: str) -> str:
    date_time = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return date_time.astimezone(TIMEZONE).strftime("%d/%m/%Y %H:%M:%S")


def load_seen_video_ids() -> set[str]:
    if not STATE_FILE.exists():
        return set()

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return set(data.get("seen_video_ids", []))
    except (json.JSONDecodeError, OSError) as error:
        write_log(f"Lỗi đọc state file: {error}")
        return set()


def save_seen_video_ids(video_ids: set[str]) -> None:
    data = {
        "channel_id": CHANNEL_ID,
        "uploads_playlist_id": UPLOADS_PLAYLIST_ID,
        "seen_video_ids": sorted(video_ids),
        "updated_at": datetime.now(TIMEZONE).isoformat(),
    }

    STATE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_youtube_feed():
    last_error = None

    for attempt in range(1, 4):
        try:
            response = requests.get(
                RSS_URL,
                headers=HEADERS,
                timeout=30,
            )
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            content = response.content

            if (
                "xml" not in content_type.lower()
                and not content.lstrip().startswith(b"<?xml")
            ):
                preview = response.text[:200].replace("\n", " ")
                raise RuntimeError(
                    "YouTube không trả RSS XML. "
                    f"Content-Type: {content_type}. Nội dung đầu: {preview}"
                )

            feed = feedparser.parse(content)

            if not feed.entries:
                raise RuntimeError("RSS không có video nào.")

            return feed

        except (requests.RequestException, RuntimeError) as error:
            last_error = error

            if attempt < 3:
                wait_seconds = round(5 * attempt + uniform(0, 3), 1)
                message = (
                    f"Lần thử {attempt}/3 lỗi: {error}. "
                    f"Chờ {wait_seconds} giây rồi thử lại."
                )
                print(message)
                write_log(message)
                time.sleep(wait_seconds)

    raise RuntimeError(
        f"Không tải được YouTube RSS sau 3 lần thử. Lỗi cuối: {last_error}"
    )


def send_telegram(message: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError(
            "Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID trong file .env"
        )

    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={
            "chat_id": int(CHAT_ID),
            "text": message,
            "disable_web_page_preview": False,
        },
        timeout=30,
    )
    response.raise_for_status()


def send_video_list(feed, entries, title_prefix: str = "") -> None:
    """Gửi danh sách video (tối đa 15) dưới dạng 1 tin nhắn."""
    lines = []

    for i, entry in enumerate(entries, start=1):
        video_url = f"https://www.youtube.com/watch?v={entry.yt_videoid}"
        published_str = format_time(entry.published)

        lines.append(
            f"{i}. {entry.title}\n"
            f"   📅 {published_str}\n"
            f"   🔗 {video_url}\n"
        )

    header = (
        f"📺 {title_prefix}15 video mới nhất từ: {feed.feed.title}\n\n"
    )

    message = header + "\n".join(lines)

    send_telegram(message)


def main() -> None:
    # Gửi tin test Telegram mỗi lần chạy
    test_message = (
        f"🔔 [TEST] Bot YouTube đang chạy.\n"
        f"Thời gian: {datetime.now(TIMEZONE):%d/%m/%Y %H:%M:%S}"
    )
    try:
        send_telegram(test_message)
        print("Đã gửi tin test vào Telegram.")
        write_log("Gửi tin test Telegram thành công.")
    except Exception as error:
        print(f"Không gửi được tin test Telegram: {error}")
        write_log(f"Lỗi gửi tin test Telegram: {error}")
        # Vẫn tiếp tục chạy kiểm tra YouTube

    try:
        feed = get_youtube_feed()
    except RuntimeError as error:
        print(f"Lỗi kết nối YouTube: {error}")
        write_log(f"Lỗi YouTube: {error}")
        return

    if feed.bozo:
        warning = f"Cảnh báo RSS XML: {feed.bozo_exception}"
        print(warning)
        write_log(warning)

    current_video_ids = {entry.yt_videoid for entry in feed.entries}
    seen_video_ids = load_seen_video_ids()

    # Lần đầu chạy: gửi 15 video mới nhất + khởi tạo state
    if not STATE_FILE.exists():
        save_seen_video_ids(current_video_ids)

        # Lấy tối đa 15 video mới nhất (mới nhất ở đầu danh sách entries)
        latest_entries = list(feed.entries)[:15]

        send_video_list(feed, latest_entries, title_prefix="🚀 Khởi tạo: Gửi ")

        init_message = (
            f"✅ Đã khởi tạo theo dõi kênh YouTube: {feed.feed.title}\n"
            f"📌 Đã ghi nhớ {len(current_video_ids)} video hiện có.\n"
            "Từ lần chạy sau, bot chỉ gửi video mới cập nhật."
        )
        send_telegram(init_message)

        print("Đã khởi tạo, gửi 15 video và xác nhận Telegram.")
        write_log("Khởi tạo theo dõi và gửi Telegram thành công.")
        return

    # Các lần chạy sau: kiểm tra video mới
    new_videos = [
        entry
        for entry in reversed(feed.entries)
        if entry.yt_videoid not in seen_video_ids
    ]

    if not new_videos:
        message = (
            f"[{datetime.now(TIMEZONE):%d/%m/%Y %H:%M:%S}] "
            "Chưa có video mới."
        )
        print(message)
        write_log(message)

        try:
            send_telegram(message)
        except Exception as error:
            print(f"Không gửi được tin 'Chưa có video mới': {error}")
            write_log(f"Lỗi gửi tin 'Chưa có video mới': {error}")

        return

    print(f"Phát hiện {len(new_videos)} video mới.")

    for entry in new_videos:
        video_url = f"https://www.youtube.com/watch?v={entry.yt_videoid}"

        message = (
            f"📺 Video mới từ: {feed.feed.title}\n\n"
            f"🎬 {entry.title}\n"
            f"📅 Đăng: {format_time(entry.published)}\n"
            f"🔗 {video_url}"
        )

        try:
            send_telegram(message)
        except (requests.RequestException, RuntimeError) as error:
            error_message = (
                f"Không gửi được Telegram cho video '{entry.title}': {error}"
            )
            print(error_message)
            write_log(error_message)
            print("Video chưa bị đánh dấu đã gửi; lần chạy sau sẽ thử lại.")
            return

        print(f"Đã gửi Telegram: {entry.title}")
        write_log(f"Đã gửi Telegram: {entry.yt_videoid} | {entry.title}")

        seen_video_ids.add(entry.yt_videoid)
        save_seen_video_ids(seen_video_ids)

    print("Hoàn tất kiểm tra.")


if __name__ == "__main__":
    main()