"""De check-loop met een nepnetwerk.

Het netwerk wordt vervangen door een tabel prijzen per aanbod, zodat de loop
zelf te testen is: wat wordt weggeschreven, wat telt als een melding, en wat
gebeurt er met een winkel die een keer weigert.
"""

import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from pricewatch import checker, deals, fetch  # noqa: E402
from pricewatch.extract import Candidate  # noqa: E402
from pricewatch.store import Store  # noqa: E402

DAY = 86400


class FakeNetwork:
    """Answers with the price each offer was given, or with a failure."""

    def __init__(self):
        self.prices = {}
        self.errors = {}
        self.calls = []

    def __call__(self, url, timeout=12.0, retries=2):
        self.calls.append(url)
        return fetch.Response(url=url, status=200, body="<html></html>")

    def read_price(self, html, url, offer, want):
        """Same shape as the real read_price: a candidate and a note."""
        key = offer["id"]
        if key in self.errors:
            raise fetch.FetchError(self.errors[key])
        if key not in self.prices:
            return None, "geen prijs gevonden op deze pagina"
        return (
            Candidate(
                price=self.prices[key],
                source="json-ld",
                title=f"{want} bij {offer['shop']}",
                url=f"{url}/product/{key}",
            ),
            "",
        )


class CheckerTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.dir.name, "pricewatch.db"))
        self.net = FakeNetwork()
        self.real_get = fetch.get
        self.real_read = checker.read_price
        fetch.get = self.net
        checker.read_price = self.net.read_price

    def tearDown(self):
        fetch.get = self.real_get
        checker.read_price = self.real_read
        self.store.close()
        self.dir.cleanup()

    def watch(self, name="AirPods Pro 2", target=None, threshold=None, floor=None):
        watch = self.store.add_watch(name, target=target, threshold=threshold)
        if floor is not None:
            self.store.update_watch(watch["id"], floor=floor)
        amazon = self.store.add_offer(watch["id"], "amazon-nl", "search", "airpods pro 2")
        coolblue = self.store.add_offer(watch["id"], "coolblue", "search", "airpods pro 2")
        return self.store.watch(watch["id"]), amazon, coolblue


class TestCheckWatch(CheckerTestCase):
    def test_cheapest_shop_wins(self):
        watch, amazon, coolblue = self.watch()
        self.net.prices[amazon["id"]] = 249.0
        self.net.prices[coolblue["id"]] = 219.0
        result = checker.check_watch(self.store, watch)
        self.assertEqual(result.best_price, 219.0)
        self.assertEqual(result.best_shop, "coolblue")
        self.assertEqual(self.store.watch(watch["id"])["best_url"].endswith(coolblue["id"]), True)

    def test_first_check_has_no_deal(self):
        watch, amazon, _ = self.watch(target=199.0)
        self.net.prices[amazon["id"]] = 249.0
        result = checker.check_watch(self.store, watch)
        self.assertEqual(result.deals, [])

    def test_second_check_below_target_raises_one_deal(self):
        watch, amazon, _ = self.watch(target=249.0)
        self.net.prices[amazon["id"]] = 259.0
        checker.check_watch(self.store, watch)
        self.net.prices[amazon["id"]] = 239.0
        result = checker.check_watch(self.store, self.store.watch(watch["id"]))
        self.assertEqual(len(result.deals), 1)
        self.assertEqual(result.deals[0].kind, deals.KIND_TARGET)

class TestBestPrice(CheckerTestCase):
    """'Beste prijs nu' is de prijs van de laatste check, niet het record."""

    def test_stijgende_prijs_volgt_de_winkel(self):
        watch, amazon, _ = self.watch(name="Hakle toiletpapier")
        self.net.prices[amazon["id"]] = 11.48
        checker.check_watch(self.store, watch)
        self.net.prices[amazon["id"]] = 24.99
        result = checker.check_watch(self.store, self.store.watch(watch["id"]))
        self.assertEqual(result.best_price, 24.99)
        self.assertEqual(self.store.watch(watch["id"])["best_price"], 24.99)

    def test_record_blijft_staan(self):
        watch, amazon, _ = self.watch(name="Hakle toiletpapier")
        self.net.prices[amazon["id"]] = 11.48
        checker.check_watch(self.store, watch)
        self.net.prices[amazon["id"]] = 24.99
        checker.check_watch(self.store, self.store.watch(watch["id"]))
        self.assertEqual(self.store.watch(watch["id"])["low_price"], 11.48)

    def test_stilte_winkel_verschrijft_niets(self):
        watch, amazon, _ = self.watch(name="Hakle toiletpapier")
        self.net.prices[amazon["id"]] = 11.48
        checker.check_watch(self.store, watch)
        self.net.errors[amazon["id"]] = "shop houdt ons tegen (403)"
        result = checker.check_watch(self.store, self.store.watch(watch["id"]))
        self.assertEqual(result.best_price, 11.48)
        self.assertEqual(self.store.watch(watch["id"])["best_price"], 11.48)

    def test_ongewijzigde_prijs_blijft_staan(self):
        # Een check schrijft alleen bij een nieuwe prijs; de weergave mag daar
        # niet door leeg worden.
        watch, amazon, _ = self.watch(name="Hakle toiletpapier")
        self.net.prices[amazon["id"]] = 11.48
        checker.check_watch(self.store, watch)
        self.assertEqual(self.store.watch(watch["id"])["best_price"], 11.48)
        checker.check_watch(self.store, self.store.watch(watch["id"]))
        self.assertEqual(self.store.watch(watch["id"])["best_price"], 11.48)

    def test_verkeerde_meting_geneest_zichzelf(self):
        watch, amazon, _ = self.watch(name="Hakle toiletpapier")
        self.net.prices[amazon["id"]] = 1.15
        checker.check_watch(self.store, watch)
        self.net.prices[amazon["id"]] = 11.48
        result = checker.check_watch(self.store, self.store.watch(watch["id"]))
        self.assertEqual(result.best_price, 11.48)
        self.assertEqual(self.store.watch(watch["id"])["best_price"], 11.48)


class TestCheckWatch(CheckerTestCase):
    def test_broken_shop_does_not_stop_the_others(self):
        watch, amazon, coolblue = self.watch()
        self.net.prices[amazon["id"]] = 249.0
        self.net.errors[coolblue["id"]] = "shop houdt ons tegen (403)"
        result = checker.check_watch(self.store, watch)
        self.assertEqual(result.best_price, 249.0)
        self.assertEqual(result.ok, 1)
        self.assertEqual(len(result.errors), 1)
        stored = self.store.watch(watch["id"])["offers"]
        broken = [o for o in stored if o["shop"] == "coolblue"][0]
        self.assertIn("403", broken["last_error"])

    def test_unchanged_price_is_not_written_again(self):
        watch, amazon, _ = self.watch()
        self.net.prices[amazon["id"]] = 249.0
        checker.check_watch(self.store, watch)
        again = checker.check_watch(self.store, self.store.watch(watch["id"]))
        self.assertEqual(len(again.offers[0].price and self.store.history(watch["id"]) or []), 1)
        self.assertFalse(again.offers[0].written)

    def test_dry_run_writes_nothing(self):
        watch, amazon, _ = self.watch()
        self.net.prices[amazon["id"]] = 249.0
        checker.check_watch(self.store, watch, dry_run=True)
        self.assertEqual(self.store.history(watch["id"]), [])

    def test_floor_rejects_an_accessory_price(self):
        watch, amazon, _ = self.watch(floor=100.0)
        self.net.prices[amazon["id"]] = 4.84
        result = checker.check_watch(self.store, watch)
        self.assertEqual(result.best_price, None)
        self.assertIn("bodem", result.errors[0])
        self.assertEqual(self.store.history(watch["id"]), [])

    def test_watch_without_offers_says_so(self):
        watch = self.store.add_watch("Leeg")
        result = checker.check_watch(self.store, watch)
        self.assertEqual(result.errors, ["geen winkels"])


class TestCheckAll(CheckerTestCase):
    def test_only_one_watch(self):
        first, amazon, _ = self.watch("AirPods Pro 2")
        second, coolblue, _ = self.watch("MX Master 3S")
        self.net.prices[amazon["id"]] = 249.0
        self.net.prices[coolblue["id"]] = 79.0
        results = checker.check_all(self.store, only=first["id"])
        self.assertEqual([r.watch_id for r in results], [first["id"]])

    def test_progress_is_reported(self):
        watch, amazon, _ = self.watch()
        self.net.prices[amazon["id"]] = 249.0
        seen = []
        checker.check_all(self.store, on_progress=seen.append)
        self.assertTrue(any("AirPods" in line for line in seen))


class TestNotification(CheckerTestCase):
    def test_notify_runs_once_per_deal(self):
        import shutil
        import subprocess

        calls = []

        class Result:
            returncode = 0

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return Result()

        real_run = subprocess.run
        real_which = shutil.which
        subprocess.run = fake_run
        shutil.which = lambda name: "/usr/bin/notify-send" if name == "notify-send" else real_which(name)
        try:
            self.assertTrue(checker.send_notification(deals.Deal(
                kind=deals.KIND_TARGET, price=199.0, shop="amazon-nl", url="https://a",
                title="", name="AirPods Pro 2", text="doelprijs bereikt",
            )))
        finally:
            subprocess.run = real_run
            shutil.which = real_which
        self.assertEqual(len(calls), 1)
        self.assertIn("AirPods Pro 2", calls[0])
        self.assertIn("https://a", calls[0][-1])


class TestHistoryMath(CheckerTestCase):
    def test_drop_is_measured_against_the_past(self):
        watch, amazon, _ = self.watch(threshold=10)
        now = int(time.time())
        for days_ago in (3, 2, 1):
            self.store.record_price(
                watch["id"], amazon["id"], 300.0, "amazon-nl", "https://a", when=now - days_ago * DAY
            )
        self.net.prices[amazon["id"]] = 200.0
        result = checker.check_watch(self.store, self.store.watch(watch["id"]))
        self.assertEqual([d.drop_pct for d in result.deals], [33.3])


if __name__ == "__main__":
    unittest.main()

class TestRunLock(CheckerTestCase):
    """Twee checks tegelijk is precies wat een winkel niet wil zien."""

    def test_second_run_is_refused(self):
        with checker.run_lock(self.store) as first:
            self.assertTrue(first)
            with checker.run_lock(self.store) as second:
                self.assertFalse(second, "een tweede check moet wijken")

    def test_lock_is_free_again_afterwards(self):
        with checker.run_lock(self.store) as first:
            self.assertTrue(first)
        with checker.run_lock(self.store) as second:
            self.assertTrue(second, "na afloop mag de volgende run weer")

    def test_two_databases_do_not_block_each_other(self):
        other = Store(os.path.join(self.dir.name, "other", "pricewatch.db"))
        try:
            with checker.run_lock(self.store):
                with checker.run_lock(other) as acquired:
                    self.assertTrue(acquired)
        finally:
            other.close()


class TestSoftNotes(CheckerTestCase):
    """Een accessoiresprijs is geen winkel die weigert: geen rode bar."""

    def test_floor_note_is_soft(self):
        watch, amazon, coolblue = self.watch(floor=100.0)
        self.store.remove_offer(coolblue["id"])  # alleen Amazon doet mee
        self.net.prices[amazon["id"]] = 4.84
        checker.check_watch(self.store, watch)
        from pricewatch import store as store_mod

        offer = self.store.watch(watch["id"])["offers"][0]
        self.assertTrue(store_mod.is_soft(offer["last_error"]), offer["last_error"])
        self.assertEqual(self.store.status()["broken"], [])
        self.assertIn("onder je bodem", offer["last_error"])

    def test_real_failure_is_not_soft(self):
        watch, amazon, coolblue = self.watch()
        self.store.remove_offer(amazon["id"])
        self.net.errors[coolblue["id"]] = "shop houdt ons tegen (403)"
        checker.check_watch(self.store, watch)
        from pricewatch import store as store_mod

        self.assertFalse(store_mod.is_soft("shop houdt ons tegen (403)"))
        broken = self.store.status()["broken"]
        self.assertEqual([b["shop"] for b in broken], ["coolblue"])
