import asyncio
import tempfile
import unittest
from pathlib import Path

from storage import GifticonStore


class GifticonStoreTests(unittest.TestCase):
    def test_lifecycle_and_search(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GifticonStore(Path(directory) / "gifticons.db")

            async def scenario() -> None:
                await store.add(10, 100, "스타벅스 아메리카노")
                gifticon = await store.get_by_source(10)
                self.assertIsNotNone(gifticon)
                self.assertEqual(gifticon.status, "available")
                self.assertTrue(await store.set_expiry(10, "2026-12-31"))
                self.assertEqual((await store.get_by_source(10)).expiry_date, "2026-12-31")
                self.assertTrue(await store.set_expiry(10, None))
                self.assertIsNone((await store.get_by_source(10)).expiry_date)
                self.assertTrue(await store.set_title(10, "아메리카노 교환권"))
                self.assertEqual((await store.get_by_source(10)).title, "아메리카노 교환권")
                self.assertTrue(await store.set_title_and_expiry(10, "스타벅스 아메리카노", "2026-06-17"))
                updated = await store.get_by_source(10)
                self.assertEqual(updated.title, "스타벅스 아메리카노")
                self.assertEqual(updated.expiry_date, "2026-06-17")

                self.assertTrue(await store.mark_used(10, "테스터"))
                self.assertFalse(await store.mark_used(10, "다른 사용자"))
                self.assertEqual((await store.get_by_archive(100)).used_by, "테스터")

                await store.restore(10, 11)
                self.assertIsNone(await store.get_by_source(10))
                self.assertEqual((await store.get_by_source(11)).status, "available")
                self.assertEqual(len(await store.list(query="스타")), 1)

                settings = await store.get_notification_settings()
                self.assertTrue(settings.enabled)
                self.assertEqual(settings.days, 7)
                self.assertEqual(settings.send_time, "09:00")
                await store.set_notification_settings(False, 14, "08:30")
                settings = await store.get_notification_settings()
                self.assertFalse(settings.enabled)
                self.assertEqual(settings.days, 14)
                self.assertEqual(settings.send_time, "08:30")

            asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
