"""Unit tests for parser, config, state manager, and Telegram formatter."""

import tempfile
import unittest
from pathlib import Path

from config import parse_cpv_codes
from document_parser import (
    FALLBACK_DELIVERY_TEXT,
    extract_delivery_from_text,
    is_delivery_missing_or_redirect,
)
from notifier import format_message
from scraper import Announcement
from state_manager import StateManager


class MonitorTests(unittest.TestCase):
    def test_parse_cpv_codes_dedup_and_order(self):
        raw = "30216110\n30237240, 30216110 invalid 32250000"
        self.assertEqual(parse_cpv_codes(raw), ["30216110", "30237240", "32250000"])

    def test_is_delivery_missing_or_redirect(self):
        self.assertTrue(is_delivery_missing_or_redirect(""))
        self.assertTrue(is_delivery_missing_or_redirect("იხ. სატენდერო დოკუმენტაცია"))
        self.assertTrue(is_delivery_missing_or_redirect("მოცემულია სატენდერო დოკუმენტაციაში"))
        self.assertFalse(is_delivery_missing_or_redirect("ხელშეკრულების გაფორმებიდან 30 კალენდარული დღე"))

    def test_extract_delivery_from_text(self):
        sample = (
            "1.2 ტექნიკური პარამეტრები: 10 ცალი.\n"
            "1.4 საქონლის მიწოდების ვადა: ხელშეკრულების გაფორმებიდან 45 კალენდარული დღის განმავლობაში.\n"
            "4.1 საგარანტიო ვადა: 12 თვე.\n"
        )
        extracted = extract_delivery_from_text(sample)
        self.assertIsNotNone(extracted)
        self.assertIn("45 კალენდარული დღის", extracted)

    def test_extract_delivery_skips_warranty_and_payment(self):
        sample = (
            "საგარანტიო ვადა: 12 თვე.\n"
            "ანგარიშსწორების ვადა: 10 სამუშაო დღე.\n"
        )
        self.assertIsNone(extract_delivery_from_text(sample))

    def test_state_manager_persistence_and_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            sm = StateManager(str(path))
            self.assertFalse(sm.is_seen("NAT260025059"))
            sm.mark_seen("NAT260025059")
            sm.save()

            sm2 = StateManager(str(path))
            self.assertTrue(sm2.is_seen("NAT260025059"))
            self.assertEqual(sm2.count, 1)

    def test_format_message_template(self):
        item = Announcement(
            procurement_type="ელექტრონული ტენდერი აუქციონის გარეშე(NAT)",
            announcement_number="NAT260025059",
            buyer="შპს ,,ბათუმის წყალი\"",
            published_date="26.11.2026 14:32",
            start_date="02.12.2026 00:00",
            end_date="04.12.2026 12:30",
            estimated_value="29,900.00 GEL",
            delivery_terms=FALLBACK_DELIVERY_TEXT,
            direct_url="https://tenders.procurement.gov.ge/public/?go=716078&lang=ge",
        )
        msg = format_message(item)
        self.assertIn("📌 შესყიდვის ტიპი: ელექტრონული ტენდერი აუქციონის გარეშე(NAT)", msg)
        self.assertIn("🆔 განცხადების ნომერი: NAT260025059", msg)
        self.assertIn("🚚 მიწოდების ვადები: იხილეთ სატენდერო დოკუმენტაცია", msg)
        self.assertIn("🔗 ბმული: https://tenders.procurement.gov.ge/public/?go=716078&lang=ge", msg)


if __name__ == "__main__":
    unittest.main()
