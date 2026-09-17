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

LOG_FILE = BASE_DIR / "youtube_notifier.log"
TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0 Safari/537.36"
    ),
    "Accept": "application/atom+xml,application/xml,text/xml,*/*",
}

# Danh sách 3 kênh bạn cần (hardcode)
# name, channel_id, state_file, telegram_chat_id
CHANNELS = [
    {
        "name": "Better Version VN",
        "channel_id": "UCTcEd3nksulFiz-iL8wH9-Q",  # thay bằng Channel ID thật nếu cần
        "state_file": "youtube_state_betterversion.json",
        "telegram_chat_id": "481749944",
    },
    {
        "name": "Phê Phim",
        "channel_id": "UCAJ9i1NhhlnosAGu7QcBEtw",
        "state_file": "youtube_state_phephim.json",
        "telegram_chat_id": "481749944",
    },
    {
        "name": "CD Media Khám Phá",
        "channel_id": "UCi_fjI2-_8eZ2XgSAU5gWXQ",  # thay bằng Channel ID thật nếu cần
        "state_file": "youtube_state_cdmedia.json",
        "telegram_chat_id": "481749944",
    },
]


def write_log(message: str) -> None:
    timestamp = datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    with LOG_FILE.open("a", encoding="utf-8") as file:
        file.write(f"{timestamp} | {message}\n")


def format_time(value: str) -> str:
    date_time = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return date_time.astimezone(TIMEZONE).strftime("%d/%m/%Y %H:%M:%S")


def load_seen_video_ids(state_file: Path) -> set[str]:
    if not state_file.exists():
        return set()

    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        return set(data.get("seen_video_ids", []))
    except (json.JSONDecodeError, OSError) as error:
        write_log(f"Lỗi đọc state file {state_file.name}: {error}")
        return set()


def save_seen_video_ids(
    state_file: Path,
    channel_id: str,
    uploads_playlist_id: str,
    video_ids: set[str],
) -> None:
    data = {
        "channel_id": channel_id,
        "uploads_playlist_id": uploads_playlist_id,
        "seen_video_ids": sorted(video_ids),
        "updated_at": datetime.now(TIMEZONE).isoformat(),
    }

    state_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_youtube_feed(rss_url: str, channel_name: str):
    last_error = None

    for attempt in range(1, 4):
        try:
            response = requests.get(
                rss_url,
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
                    f"[{channel_name}] YouTube không trả RSS XML. "
                    f"Content-Type: {content_type}. Nội dung đầu: {preview}"
                )

            feed = feedparser.parse(content)

            if not feed.entries:
                raise RuntimeError(f"[{channel_name}] RSS không có video nào.")

            return feed

        except (requests.RequestException, RuntimeError) as error:
            last_error = error

            if attempt < 3:
                wait_seconds = round(5 * attempt + uniform(0, 3), 1)
                message = (
                    f"[{channel_name}] Lần thử {attempt}/3 lỗi: {error}. "
                    f"Chờ {wait_seconds} giây rồi thử lại."
                )
                print(message)
                write_log(message)
                time.sleep(wait_seconds)

    raise RuntimeError(
        f"[{channel_name}] Không tải được YouTube RSS sau 3 lần thử. Lỗi cuối: {last_error}"
    )


def send_telegram(message: str, chat_id: str) -> None:
    if not BOT_TOKEN or not chat_id:
        raise RuntimeError(
            "Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID"
        )

    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={
            "chat_id": int(chat_id),
            "text": message,
            "disable_web_page_preview": False,
        },
        timeout=30,
    )
    response.raise_for_status()


def send_video_list(feed, entries, chat_id: str, channel_name: str) -> None:
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
        f"📺 🚀 Khởi tạo: Gửi 15 video mới nhất từ: {channel_name}\n\n"
    )

    message = header + "\n".join(lines)

    send_telegram(message, chat_id)


def process_channel(channel: dict) -> None:
    name = channel["name"]
    channel_id = channel["channel_id"]
    state_file_name = channel["state_file"]
    chat_id = channel["telegram_chat_id"]

    state_file = BASE_DIR / state_file_name

    # Tính Uploads Playlist ID
    if not channel_id.startswith("UC"):
        msg = f"[{name}] Channel ID không hợp lệ: {channel_id}"
        print(msg)
        write_log(msg)
        return

    uploads_playlist_id = "UU" + channel_id[2:]
    rss_url = (
        "https://www.youtube.com/feeds/videos.xml"
        f"?playlist_id={uploads_playlist_id}"
    )

    # Gửi tin test
    test_message = (
        f"🔔 [TEST] Bot YouTube đang chạy – Kênh: {name}\n"
        f"Thời gian: {datetime.now(TIMEZONE):%d/%m/%Y %H:%M:%S}"
    )
    try:
        send_telegram(test_message, chat_id)
        print(f"[{name}] Đã gửi tin test vào Telegram.")
        write_log(f"[{name}] Gửi tin test Telegram thành công.")
    except Exception as error:
        print(f"[{name}] Không gửi được tin test Telegram: {error}")
        write_log(f"[{name}] Lỗi gửi tin test Telegram: {error}")

    try:
        feed = get_youtube_feed(rss_url, name)
    except RuntimeError as error:
        print(f"[{name}] Lỗi kết nối YouTube: {error}")
        write_log(f"[{name}] Lỗi YouTube: {error}")
        return

    if feed.bozo:
        warning = f"[{name}] Cảnh báo RSS XML: {feed.bozo_exception}"
        print(warning)
        write_log(warning)

    current_video_ids = {entry.yt_videoid for entry in feed.entries}
    seen_video_ids = load_seen_video_ids(state_file)

    # Lần đầu chạy
    if not state_file.exists():
        save_seen_video_ids(
            state_file, channel_id, uploads_playlist_id, current_video_ids
        )

        latest_entries = list(feed.entries)[:15]
        send_video_list(feed, latest_entries, chat_id, name)

        init_message = (
            f"✅ Đã khởi tạo theo dõi kênh YouTube: {name}\n"
            f"📌 Đã ghi nhớ {len(current_video_ids)} video hiện có.\n"
            "Từ lần chạy sau, bot chỉ gửi video mới cập nhật."
        )
        send_telegram(init_message, chat_id)

        print(f"[{name}] Đã khởi tạo, gửi 15 video và xác nhận Telegram.")
        write_log(f"[{name}] Khởi tạo theo dõi và gửi Telegram thành công.")
        return

    # Các lần chạy sau
    new_videos = [
        entry
        for entry in reversed(feed.entries)
        if entry.yt_videoid not in seen_video_ids
    ]

    if not new_videos:
        message = (
            f"[{datetime.now(TIMEZONE):%d/%m/%Y %H:%M:%S}] "
            f"[{name}] Chưa có video mới."
        )
        print(message)
        write_log(message)

        try:
            send_telegram(message, chat_id)
        except Exception as error:
            print(f"[{name}] Không gửi được tin 'Chưa có video mới': {error}")
            write_log(f"[{name}] Lỗi gửi tin 'Chưa có video mới': {error}")

        return

    print(f"[{name}] Phát hiện {len(new_videos)} video mới.")

    for entry in new_videos:
        video_url = f"https://www.youtube.com/watch?v={entry.yt_videoid}"

        message = (
            f"📺 Video mới từ: {name}\n\n"
            f"🎬 {entry.title}\n"
            f"📅 Đăng: {format_time(entry.published)}\n"
            f"🔗 {video_url}"
        )

        try:
            send_telegram(message, chat_id)
        except (requests.RequestException, RuntimeError) as error:
            error_message = (
                f"[{name}] Không gửi được Telegram cho video '{entry.title}': {error}"
            )
            print(error_message)
            write_log(error_message)
            print(f"[{name}] Video chưa bị đánh dấu đã gửi; lần chạy sau sẽ thử lại.")
            return

        print(f"[{name}] Đã gửi Telegram: {entry.title}")
        write_log(f"[{name}] Đã gửi Telegram: {entry.yt_videoid} | {entry.title}")

        seen_video_ids.add(entry.yt_videoid)
        save_seen_video_ids(
            state_file, channel_id, uploads_playlist_id, seen_video_ids
        )

    print(f"[{name}] Hoàn tất kiểm tra.")


def main() -> None:
    for channel in CHANNELS:
        try:
            process_channel(channel)
        except Exception as error:
            msg = f"Lỗi khi xử lý kênh {channel.get('name', '???')}: {error}"
            print(msg)
            write_log(msg)


if __name__ == "__main__":
    main()