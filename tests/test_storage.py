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

                self.assertTrue(await store.mark_used(10, "테스터"))
                self.assertFalse(await store.mark_used(10, "다른 사용자"))
                self.assertEqual((await store.get_by_archive(100)).used_by, "테스터")

                await store.restore(10, 11)
                self.assertIsNone(await store.get_by_source(10))
                self.assertEqual((await store.get_by_source(11)).status, "available")
                self.assertEqual(len(await store.list(query="스타")), 1)

            asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
