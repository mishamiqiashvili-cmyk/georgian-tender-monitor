"""Telegram Bot API notifier."""

import logging
import time
import requests

from scraper import Announcement

logger = logging.getLogger(__name__)


def format_message(item: Announcement) -> str:
    """Render announcement into the exact Georgian Telegram notification template."""
    return (
        f"📌 შესყიდვის ტიპი: {item.procurement_type}\n"
        f"🆔 განცხადების ნომერი: {item.announcement_number}\n"
        f"🏢 შემსყიდველი: {item.buyer}\n"
        f"📅 შესყიდვის გამოცხადების თარიღი: {item.published_date}\n"
        f"🟢 წინადადებების მიღება იწყება: {item.start_date}\n"
        f"🔴 წინადადებების მიღება მთავრდება: {item.end_date}\n"
        f"💰 შესყიდვის სავარაუდო ღირებულება: {item.estimated_value}\n"
        f"🚚 მიწოდების ვადები: {item.delivery_terms}\n"
        f"🔗 ბმული: {item.direct_url}"
    )


class TelegramNotifier:
    """Sends formatted messages to a Telegram chat with retry & rate-limit handling."""

    def __init__(self, bot_token: str, chat_id: str, timeout: int = 20) -> None:
        self.api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self.chat_id = chat_id
        self.timeout = timeout

    def send(self, item: Announcement, max_retries: int = 3) -> bool:
        payload = {
            "chat_id": self.chat_id,
            "text": format_message(item),
            "disable_web_page_preview": True,
        }
        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.post(self.api_url, json=payload, timeout=self.timeout)
                if resp.status_code == 429:
                    retry_after = resp.json().get("parameters", {}).get("retry_after", 3)
                    logger.warning("Telegram rate limit; sleeping %ss", retry_after)
                    time.sleep(float(retry_after) + 0.5)
                    continue
                resp.raise_for_status()
                body = resp.json()
                if body.get("ok"):
                    time.sleep(0.35)
                    return True
                logger.error("Telegram API error for %s: %s", item.announcement_number, body)
            except Exception as exc:
                logger.warning(
                    "Telegram send attempt %d/%d failed for %s: %s",
                    attempt,
                    max_retries,
                    item.announcement_number,
                    exc,
                )
                if attempt < max_retries:
                    time.sleep(1.5 * attempt)
        return False
