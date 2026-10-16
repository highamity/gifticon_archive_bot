import os
import unittest
import unittest.mock
from datetime import date

os.environ.setdefault("GIFTICON_ACTIVE_CHAT_ID", "-1001234567890")
os.environ.setdefault("GIFTICON_ARCHIVE_CHAT_ID", "-1009876543210")

try:
    from bot import expiry_from_text, gifticon_summary, ocr_rapid_expiry
except ModuleNotFoundError as error:
    BOT_IMPORT_ERROR = str(error)
else:
    BOT_IMPORT_ERROR = ""


@unittest.skipIf(BOT_IMPORT_ERROR, f"bot dependencies unavailable: {BOT_IMPORT_ERROR}")
class ExpiryParseTests(unittest.TestCase):
    def test_barcode_digits_are_ignored(self) -> None:
        self.assertIsNone(expiry_from_text("4520000903881495"))
        self.assertIsNone(expiry_from_text("쿠폰 번호 442113811050210929"))

    def test_barcode_does_not_shadow_real_date(self) -> None:
        text = "[백원딜] CU 3천원권\n2026.08.18-2026.09.15(18일 남음)\n4520000903881495"
        self.assertEqual(expiry_from_text(text), date(2026, 9, 15))

    def test_validity_range_picks_end_date(self) -> None:
        self.assertEqual(
            expiry_from_text("유효기간 : 2026.05.22 ~ 2026.08.23"),
            date(2026, 8, 23),
        )

    def test_spaced_korean_date(self) -> None:
        self.assertEqual(
            expiry_from_text("유 효 기 간 2026 년 10 월 31 일 까 지"),
            date(2026, 10, 31),
        )

    def test_ocr_glyph_confusion_fallback(self) -> None:
        self.assertEqual(expiry_from_text("유효기간 2026.O5.12까지"), date(2026, 5, 12))
        self.assertEqual(expiry_from_text("2O26년 10월 31일까지"), date(2026, 10, 31))

    def test_no_date_returns_none(self) -> None:
        self.assertIsNone(expiry_from_text("교환처: 스타벅스\n아메리카노"))
        self.assertIsNone(expiry_from_text(""))

    def test_fullwidth_punctuation(self) -> None:
        self.assertEqual(expiry_from_text("2027．08．14까지"), date(2027, 8, 14))
        self.assertEqual(
            expiry_from_text("2026．09．07～2026．09．11"), date(2026, 9, 11)
        )

    def test_barcode_on_next_line_does_not_eat_date(self) -> None:
        text = "ATWOSOMEPLACE\n0\n2027.08.14\n994548997183\nSmilecon"
        self.assertEqual(expiry_from_text(text), date(2027, 8, 14))
        text = "2026.09.07~2026.09.11\n442113811050210929"
        self.assertEqual(expiry_from_text(text), date(2026, 9, 11))


@unittest.skipIf(BOT_IMPORT_ERROR, f"bot dependencies unavailable: {BOT_IMPORT_ERROR}")
class SpacedSummaryTests(unittest.TestCase):
    def test_spaced_merchant_keywords(self) -> None:
        text = (
            "사 용 처 카카오페이\n"
            "상 품 명 [카카오페이] 카카오페이포인트 5,000P\n"
            "유효기간 2026년 10월 31일까지"
        )
        summary = gifticon_summary(text, date(2026, 10, 31))
        self.assertIn("카카오페이", summary.split(" / ")[0])
        self.assertIn("2026-10-31", summary)

    def test_spaced_brand_matching(self) -> None:
        text = "사 용 처 카 카 오 페 이\n상 품 명 카 카 오 페 이 포 인트 5\n유 효 기 간 2026 년 10 월 31 일"
        summary = gifticon_summary(text, date(2026, 10, 31))
        self.assertTrue(summary.startswith("카카오페이"))
        self.assertIn("2026-10-31", summary)


@unittest.skipIf(BOT_IMPORT_ERROR, f"bot dependencies unavailable: {BOT_IMPORT_ERROR}")
class RapidOcrFallbackTests(unittest.TestCase):
    def test_rapid_text_used_for_expiry_only(self) -> None:
        import bot

        class FakeEngine:
            def __call__(self, image_bytes):
                return (
                    [
                        [None, "2026.08.18~2026.09.15"],
                        [None, "4520000903881495"],
                    ],
                    None,
                )

        with unittest.mock.patch.object(bot, "_rapid_engine", return_value=FakeEngine()):
            self.assertEqual(
                ocr_rapid_expiry(b"fake-image-bytes"), date(2026, 9, 15)
            )

    def test_rapid_missing_returns_none(self) -> None:
        import bot

        with unittest.mock.patch.object(
            bot, "_rapid_engine", side_effect=ImportError("no rapidocr")
        ):
            self.assertIsNone(ocr_rapid_expiry(b"fake-image-bytes"))

    def test_rapid_empty_result_returns_none(self) -> None:
        import bot

        class FakeEngine:
            def __call__(self, image_bytes):
                return (None, None)

        with unittest.mock.patch.object(bot, "_rapid_engine", return_value=FakeEngine()):
            self.assertIsNone(ocr_rapid_expiry(b"fake-image-bytes"))


if __name__ == "__main__":
    unittest.main()
