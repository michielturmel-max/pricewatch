"""De regels die bepalen wanneer pricewatch iemand stoort."""

import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from pricewatch import deals  # noqa: E402
from pricewatch.store import Store  # noqa: E402

DAY = 86400


class DealTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.dir.name, "pricewatch.db"))

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def watch(self, name="AirPods Pro 2", target=None, threshold=None, muted=False):
        watch = self.store.add_watch(name, target=target, threshold=threshold, muted=muted)
        self.offer = self.store.add_offer(watch["id"], "amazon-nl", "search", "airpods pro 2")
        return watch

    def history(self, watch, prices, days_apart=3):
        now = int(time.time())
        for index, price in enumerate(prices):
            self.store.record_price(
                watch["id"], self.offer["id"], price, "amazon-nl", "https://a", when=now - (len(prices) - index) * days_apart * DAY
            )
        return self.store.watch(watch["id"])


class TestTarget(DealTestCase):
    def test_target_price_raises_one_deal(self):
        watch = self.watch(target=199.0)
        deal = deals.evaluate(self.store, watch, 189.0, "amazon-nl", "https://a")
        self.assertEqual(deal.kind, deals.KIND_TARGET)
        self.assertIn("doelprijs bereikt", deal.text)

    def test_above_target_says_nothing(self):
        watch = self.watch(target=199.0)
        self.assertIsNone(deals.evaluate(self.store, watch, 219.0, "amazon-nl", "https://a"))

    def test_target_deal_repeats_at_most_once_a_day(self):
        watch = self.watch(target=199.0)
        now = int(time.time())
        self.assertTrue(deals.evaluate(self.store, watch, 189.0, "amazon-nl", "https://a", now=now))
        self.assertIsNone(deals.evaluate(self.store, watch, 188.0, "amazon-nl", "https://a", now=now))
        tomorrow = deals.evaluate(self.store, watch, 187.0, "amazon-nl", "https://a", now=now + DAY)
        self.assertIsNotNone(tomorrow, "morgen mag het opnieuw")

    def test_muted_watch_never_notifies(self):
        watch = self.watch(target=199.0, muted=True)
        self.assertIsNone(deals.evaluate(self.store, watch, 189.0, "amazon-nl", "https://a"))


class TestAllTimeLow(DealTestCase):
    def test_first_price_is_not_a_record(self):
        watch = self.watch()
        self.assertIsNone(deals.evaluate(self.store, watch, 249.0, "amazon-nl", "https://a"))

    def test_cheaper_than_the_record_raises_a_deal(self):
        watch = self.history(self.watch(), [249.0])
        deal = deals.evaluate(self.store, watch, 199.0, "amazon-nl", "https://a")
        self.assertEqual(deal.kind, deals.KIND_LOW)
        self.assertIn("nieuw laagste", deal.text)

    def test_same_low_price_is_quiet(self):
        watch = self.history(self.watch(), [249.0])
        self.assertTrue(deals.evaluate(self.store, watch, 199.0, "amazon-nl", "https://a"))
        self.assertIsNone(deals.evaluate(self.store, watch, 199.0, "amazon-nl", "https://a"))

    def test_target_wins_over_the_record(self):
        watch = self.history(self.watch(target=249.0), [299.0])
        deal = deals.evaluate(self.store, watch, 249.0, "amazon-nl", "https://a")
        self.assertEqual(deal.kind, deals.KIND_TARGET)


class TestDrop(DealTestCase):
    """A drop below the reference without setting a new record.

    The record is €200 and the reference €400, so a price under €400 is not
    automatically the lowest ever seen and the threshold rule gets to speak.
    """

    def setUp(self):
        super().setUp()
        self.tracked = self.history(self.watch(threshold=15), [200.0, 400.0, 500.0])
        self.reference = self.store.reference(self.tracked["id"])
        self.assertEqual(self.reference["value"], 400.0)

    def test_drop_against_the_reference(self):
        deal = deals.evaluate(self.store, self.tracked, 250.0, "amazon-nl", "https://a", reference=self.reference)
        self.assertEqual(deal.kind, deals.KIND_DROP)
        self.assertEqual(deal.drop_pct, 37.5)
        self.assertIn("38% onder", deal.text)

    def test_small_dip_stays_quiet(self):
        self.assertIsNone(deals.evaluate(self.store, self.tracked, 380.0, "amazon-nl", "https://a", reference=self.reference))

    def test_too_little_history_keeps_quiet(self):
        fresh = self.watch(threshold=10)
        reference = self.store.reference(fresh["id"])
        self.assertEqual(reference["points"], 0)
        self.assertIsNone(deals.evaluate(self.store, fresh, 10.0, "amazon-nl", "https://a", reference=reference))

    def test_drop_measured_before_todays_write(self):
        watch = self.history(self.watch(threshold=10), [300.0, 300.0, 300.0])
        reference = self.store.reference(watch["id"])
        self.store.record_price(watch["id"], self.offer["id"], 100.0, "amazon-nl", "https://a")
        deal = deals.evaluate(
            self.store, watch, 100.0, "amazon-nl", "https://a", reference=reference, low_ever=300.0, low_known=True
        )
        self.assertEqual(deal.drop_pct, 66.7)

    def test_custom_threshold_is_used(self):
        watch = self.history(self.watch(threshold=40), [200.0, 400.0, 500.0])
        reference = self.store.reference(watch["id"])
        self.assertIsNone(deals.evaluate(self.store, watch, 260.0, "amazon-nl", "https://a", reference=reference))
        self.assertIsNotNone(deals.evaluate(self.store, watch, 170.0, "amazon-nl", "https://a", reference=reference))


class TestVerdict(DealTestCase):
    def test_first_price_is_normaal(self):
        watch = self.watch(target=199.0)
        self.store.record_price(watch["id"], self.offer["id"], 249.0, "amazon-nl", "https://a")
        watch = self.store.watch(watch["id"])
        self.assertEqual(deals.verdict(watch, self.store.stats(watch["id"])), "normaal")

    def test_target_wins_in_the_verdict(self):
        watch = self.watch(target=249.0)
        self.store.record_price(watch["id"], self.offer["id"], 239.0, "amazon-nl", "https://a")
        watch = self.store.watch(watch["id"])
        self.assertEqual(deals.verdict(watch, self.store.stats(watch["id"])), "doelprijs")

    def test_unknown_price(self):
        watch = self.watch()
        self.assertEqual(deals.verdict(watch, self.store.stats(watch["id"])), "onbekend")


class TestSparkline(DealTestCase):
    def test_flat_history_is_one_block(self):
        rows = [{"price": 10.0} for _ in range(5)]
        self.assertEqual(deals.sparkline(rows), "\u2581" * 5)

    def test_rising_history_grows(self):
        rows = [{"price": p} for p in (10.0, 20.0, 30.0)]
        line = deals.sparkline(rows)
        self.assertEqual(line[0], "\u2581")
        self.assertEqual(line[-1], "\u2588")

    def test_long_history_is_shortened(self):
        rows = [{"price": float(i)} for i in range(200)]
        self.assertEqual(len(deals.sparkline(rows, width=24)), 24)

    def test_empty_history(self):
        self.assertEqual(deals.sparkline([]), "")


if __name__ == "__main__":
    unittest.main()