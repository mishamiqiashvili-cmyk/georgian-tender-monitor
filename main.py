"""Entrypoint for the Georgian State Procurement 24/7 monitor."""

import logging
import sys

from config import load_config
from notifier import TelegramNotifier, format_message
from scraper import ProcurementScraper
from state_manager import StateManager


def configure_logging() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def run() -> int:
    configure_logging()
    logger = logging.getLogger("monitor")

    cfg = load_config()
    state = StateManager(cfg.state_file)
    logger.info("Loaded %d CPV codes; %d previously seen IDs", len(cfg.cpv_codes), state.count)

    scraper = ProcurementScraper(cfg)
    new_items = scraper.fetch_new_announcements(state.is_seen)
    logger.info("Total matched new announcements to notify: %d", len(new_items))

    if not new_items:
        if not state.path.exists():
            state.save()
        return 0

    notifier = TelegramNotifier(cfg.bot_token, cfg.chat_id, timeout=cfg.request_timeout)
    sent_count = 0
    failed_count = 0

    # Send oldest-first so chronological order is preserved in Telegram chat
    for item in reversed(new_items):
        if cfg.dry_run:
            logger.info("[DRY RUN] Would send:\n%s\n%s", format_message(item), "-" * 50)
            sent_count += 1
            continue

        if notifier.send(item, max_retries=cfg.max_retries):
            state.mark_seen(item.announcement_number)
            sent_count += 1
            logger.info("Sent notification for %s", item.announcement_number)
        else:
            failed_count += 1
            logger.error("Failed to notify for %s; will retry next run", item.announcement_number)

    if not cfg.dry_run and (sent_count > 0 or not state.path.exists()):
        state.save()

    logger.info("Run complete: sent=%d failed=%d total_seen=%d", sent_count, failed_count, state.count)
    return 1 if (failed_count > 0 and sent_count == 0) else 0


if __name__ == "__main__":
    sys.exit(run())
