"""Opslag: watches, geschiedenis en de boekhouding achter een melding."""

import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from pricewatch.store import Store, money, pct  # noqa: E402


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.dir.name, "pricewatch.db"))

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def watch_with_offer(self, name="AirPods Pro 2", target=199.0):
        watch = self.store.add_watch(name, target=target)
        offer = self.store.add_offer(watch["id"], "amazon-nl", "search", "airpods pro 2")
        return watch, offer


class TestWatches(StoreTestCase):
    def test_add_creates_readable_id(self):
        watch = self.store.add_watch("AirPods Pro 2", note="voor de auto")
        self.assertEqual(watch["id"], "airpods-pro-2")
        self.assertEqual(watch["target"], None)

    def test_same_name_twice_stays_two_watches(self):
        first = self.store.add_watch("Lamp")
        second = self.store.add_watch("Lamp")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(self.store.watches()), 2)

    def test_resolve_accepts_name(self):
        self.store.add_watch("AirPods Pro 2")
        self.assertEqual(self.store.resolve("AirPods Pro 2"), "airpods-pro-2")

    def test_resolve_unknown_raises(self):
        with self.assertRaises(KeyError):
            self.store.resolve("niks")

    def test_update_and_mute(self):
        watch, _ = self.watch_with_offer()
        self.store.update_watch(watch["id"], muted=True, threshold=15)
        again = self.store.watch(watch["id"])
        self.assertTrue(again["muted"])
        self.assertEqual(again["threshold"], 15)

    def test_update_refuses_unknown_field(self):
        watch, _ = self.watch_with_offer()
        with self.assertRaises(ValueError):
            self.store.update_watch(watch["id"], best_price=1)

    def test_remove_takes_history_with_it(self):
        watch, offer = self.watch_with_offer()
        self.store.record_price(watch["id"], offer["id"], 249.0, "amazon-nl", "https://x")
        self.store.remove_watch(watch["id"])
        self.assertEqual(self.store.watches(), [])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM prices").fetchone()[0], 0)
        with self.assertRaises(KeyError):
            self.store.history("airpods-pro-2")

    def test_offer_is_not_added_twice(self):
        watch, offer = self.watch_with_offer()
        again = self.store.add_offer(watch["id"], "amazon-nl", "search", "airpods pro 2")
        self.assertEqual(again["id"], offer["id"])
        self.assertEqual(len(self.store.watch(watch["id"])["offers"]), 1)

    def test_offer_kind_is_checked(self):
        watch, _ = self.watch_with_offer()
        with self.assertRaises(ValueError):
            self.store.add_offer(watch["id"], "amazon-nl", "magie", "x")


class TestPrices(StoreTestCase):
    def test_best_and_low_follow_the_lowest_price(self):
        watch, offer = self.watch_with_offer()
        self.store.record_price(watch["id"], offer["id"], 249.0, "amazon-nl", "https://a")
        self.store.record_price(watch["id"], offer["id"], 199.0, "amazon-nl", "https://b")
        current = self.store.watch(watch["id"])
        self.assertEqual(current["best_price"], 199.0)
        self.assertEqual(current["low_price"], 199.0)

        self.store.record_price(watch["id"], offer["id"], 249.0, "coolblue", "https://c")
        current = self.store.watch(watch["id"])
        self.assertEqual(current["best_price"], 199.0, "een duurdere prijs overschrijft de beste niet")

    def test_second_shop_can_take_the_best_price(self):
        watch, first = self.watch_with_offer()
        second = self.store.add_offer(watch["id"], "coolblue", "search", "airpods pro 2")
        self.store.record_price(watch["id"], first["id"], 249.0, "amazon-nl", "https://a")
        self.store.record_price(watch["id"], second["id"], 219.0, "coolblue", "https://b")
        current = self.store.watch(watch["id"])
        self.assertEqual((current["best_price"], current["best_shop"]), (219.0, "coolblue"))

    def test_failure_keeps_the_last_price(self):
        watch, offer = self.watch_with_offer()
        self.store.record_price(watch["id"], offer["id"], 249.0, "amazon-nl", "https://a")
        self.store.record_failure(watch["id"], offer["id"], "shop houdt ons tegen (403)")
        fresh = self.store.watch(watch["id"])
        self.assertEqual(fresh["best_price"], 249.0)
        self.assertEqual(fresh["offers"][0]["last_error"], "shop houdt ons tegen (403)")
        self.assertEqual(fresh["offers"][0]["fail_count"], 1)

    def test_history_and_daily_bests(self):
        watch, offer = self.watch_with_offer()
        now = int(time.time())
        for days_ago, price in ((2, 249.0), (1, 229.0), (0, 219.0)):
            self.store.record_price(
                watch["id"], offer["id"], price, "amazon-nl", "https://a", when=now - days_ago * 86400
            )
        rows = self.store.history(watch["id"])
        self.assertEqual([row["price"] for row in rows], [249.0, 229.0, 219.0])
        self.assertEqual(self.store.daily_bests(watch["id"])[-1], (time.strftime("%Y-%m-%d"), 219.0))

    def test_reference_is_the_median_of_daily_lows(self):
        watch, offer = self.watch_with_offer()
        now = int(time.time())
        for days_ago, price in ((3, 200.0), (2, 300.0), (1, 400.0)):
            self.store.record_price(
                watch["id"], offer["id"], price, "amazon-nl", "https://a", when=now - days_ago * 86400
            )
        self.assertEqual(self.store.reference(watch["id"])["value"], 300.0)
        self.assertEqual(self.store.daily_bests(watch["id"])[0][1], 200.0)

    def test_same_second_different_prices_are_kept(self):
        watch, offer = self.watch_with_offer()
        now = int(time.time())
        self.store.record_price(watch["id"], offer["id"], 249.0, "amazon-nl", "https://a", when=now)
        self.store.record_price(watch["id"], offer["id"], 239.0, "amazon-nl", "https://a", when=now)
        self.assertEqual(len(self.store.history(watch["id"])), 2)

    def test_reference_is_empty_without_history(self):
        watch, _ = self.watch_with_offer()
        reference = self.store.reference(watch["id"])
        self.assertIsNone(reference["value"])
        self.assertEqual(reference["points"], 0)

    def test_stats_survive_a_single_point(self):
        watch, offer = self.watch_with_offer()
        self.store.record_price(watch["id"], offer["id"], 249.0, "amazon-nl", "https://a")
        stats = self.store.stats(watch["id"])
        self.assertEqual(stats["points"], 1)
        self.assertEqual(stats["current"], 249.0)


class TestAnalysis(StoreTestCase):
    def fill(self, watch, offer, rows):
        now = int(time.time())
        for days_ago, price in rows:
            self.store.record_price(
                watch["id"], offer["id"], price, "amazon-nl", "https://a", when=now - days_ago * 86400
            )

    def test_change_meet_tegen_een_week_geleden(self):
        watch, offer = self.watch_with_offer()
        self.fill(watch, offer, [(10, 250.0), (8, 250.0), (1, 225.0)])
        self.assertEqual(self.store.change(watch["id"], 7, 200.0), -20.0)
        self.assertEqual(self.store.change(watch["id"], 30, 200.0), -20.0)

    def test_change_van_een_jonge_watch_meet_tegen_de_eerste_prijs(self):
        watch, offer = self.watch_with_offer()
        self.fill(watch, offer, [(2, 240.0), (1, 210.0)])
        self.assertEqual(self.store.change(watch["id"], 30, 210.0), -12.5)

    def test_change_zonder_prijzen_is_geen_getal(self):
        watch, _ = self.watch_with_offer()
        self.assertIsNone(self.store.change(watch["id"], 7))

    def test_shop_breakdown_per_winkel(self):
        watch, offer = self.watch_with_offer()
        second = self.store.add_offer(watch["id"], "coolblue", "search", "airpods pro 2")
        now = int(time.time())
        self.store.record_price(watch["id"], offer["id"], 249.0, "amazon-nl", "https://a", when=now - 86400)
        self.store.record_price(watch["id"], offer["id"], 239.0, "amazon-nl", "https://a", when=now)
        self.store.record_price(watch["id"], offer["id"], 219.0, "coolblue", "https://b", when=now)
        broken = self.store.shop_breakdown(watch["id"])
        self.assertEqual([item["shop"] for item in broken], ["coolblue", "amazon-nl"])
        amazon = next(item for item in broken if item["shop"] == "amazon-nl")
        self.assertEqual((amazon["low"], amazon["high"], amazon["last"], amazon["points"]), (239.0, 249.0, 239.0, 2))

    def test_analysis_geeft_periodes_en_een_halfjaarsbodem(self):
        watch, offer = self.watch_with_offer()
        self.fill(watch, offer, [(200, 300.0), (100, 280.0), (60, 320.0), (10, 240.0), (0, 250.0)])
        analysis = self.store.analysis(watch["id"])
        periods = {period["days"]: period for period in analysis["periods"]}
        self.assertEqual(periods[7]["low"], 250.0)
        self.assertEqual(periods[30]["low"], 240.0)
        self.assertEqual(periods[30]["high"], 250.0)
        self.assertEqual(periods[0]["points"], 5)
        self.assertEqual(analysis["lowest_6m"]["price"], 240.0)
        self.assertRegex(analysis["lowest_6m"]["day"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(analysis["avg_30d"], round((240.0 + 250.0) / 2, 2))

    def test_analysis_laat_de_besparing_zien(self):
        watch, offer = self.watch_with_offer()
        self.fill(watch, offer, [(20, 300.0), (0, 240.0)])
        analysis = self.store.analysis(watch["id"])
        self.assertEqual(analysis["savings"], {"amount": 60.0, "pct": 20.0})

    def test_analysis_van_een_lege_watch(self):
        watch, _ = self.watch_with_offer()
        analysis = self.store.analysis(watch["id"])
        self.assertIsNone(analysis["current"])
        self.assertEqual(analysis["daily"], [])
        self.assertIsNone(analysis["lowest_6m"])
        self.assertTrue(all(period["points"] == 0 for period in analysis["periods"]))


class TestImportExport(StoreTestCase):
    def make_watch(self):
        watch, offer = self.watch_with_offer()
        second = self.store.add_offer(watch["id"], "coolblue", "search", "airpods pro 2")
        now = int(time.time())
        self.store.record_price(watch["id"], offer["id"], 249.0, "amazon-nl", "https://a", when=now - 2 * 86400)
        self.store.record_price(watch["id"], second["id"], 219.0, "coolblue", "https://b", when=now - 86400)
        self.store.record_price(watch["id"], offer["id"], 229.0, "amazon-nl", "https://a", when=now)
        return watch

    def test_export_bevat_watches_en_prijzen(self):
        watch = self.make_watch()
        payload = self.store.export()
        self.assertEqual(payload["schema"], 2)
        self.assertEqual([item["id"] for item in payload["watches"]], [watch["id"]])
        self.assertEqual(len(payload["prices"]), 3)
        self.assertEqual({row["shop"] for row in payload["prices"]}, {"amazon-nl", "coolblue"})

    def test_export_van_een_watch_alleen(self):
        keep = self.make_watch()
        other = self.store.add_watch("Lamp")
        payload = self.store.export(keep["id"])
        self.assertEqual([item["id"] for item in payload["watches"]], [keep["id"]])
        self.assertNotIn(other["id"], {row["watch_id"] for row in payload["prices"]})

    def test_import_herstelt_de_geschiedenis(self):
        watch = self.make_watch()
        payload = self.store.export()
        with tempfile.TemporaryDirectory() as other:
            fresh = Store(os.path.join(other, "pricewatch.db"))
            try:
                counts = fresh.import_data(payload)
                self.assertEqual((counts["watches"], counts["prices"]), (1, 3))
                restored = fresh.watch(watch["id"])
                self.assertEqual(restored["name"], "AirPods Pro 2")
                self.assertEqual(restored["target"], 199.0)
                self.assertEqual(len(fresh.history(watch["id"], 0)), 3)
                self.assertEqual(fresh.watch(watch["id"])["best_price"], 229.0,
                                 "de huidige prijs komt uit de nieuwste regel, niet uit het oude dieptepunt")
                self.assertEqual(fresh.watch(watch["id"])["low_price"], 219.0)
            finally:
                fresh.close()

    def test_import_twee_keer_verdubbelt_niets(self):
        watch = self.make_watch()
        payload = self.store.export()
        self.store.import_data(payload)
        self.store.import_data(payload)
        self.assertEqual(len(self.store.history(watch["id"], 0)), 3)
        self.assertEqual(len(self.store.watches()), 1)
        self.assertEqual(len(self.store.offers(watch["id"])), 2)

    def test_import_met_vervang_gooit_de_oude_watch_weg(self):
        watch = self.make_watch()
        payload = self.store.export()
        self.store.update_watch(watch["id"], note="oude notitie")
        self.store.import_data(payload, replace=True)
        fresh = self.store.watch(watch["id"])
        self.assertEqual(fresh["note"], "")
        self.assertEqual(len(self.store.history(watch["id"], 0)), 3)

    def test_import_slaat_regels_zonder_naam_over(self):
        counts = self.store.import_data({"watches": [{"id": "leeg", "name": ""}], "prices": [{"watch_id": "x"}]})
        self.assertEqual(counts, {"watches": 0, "offers": 0, "prices": 0, "skipped": 2})


class TestNotices(StoreTestCase):
    def test_notified_once_is_true_the_first_time_only(self):
        watch, _ = self.watch_with_offer()
        self.assertTrue(self.store.notified_once(watch["id"], "doelprijs", "vandaag"))
        self.assertFalse(self.store.notified_once(watch["id"], "doelprijs", "vandaag"))
        self.assertTrue(self.store.notified_once(watch["id"], "doelprijs", "morgen"))


class TestStatus(StoreTestCase):
    def test_status_counts_a_watch_at_its_target(self):
        watch, offer = self.watch_with_offer(target=199.0)
        self.store.record_price(watch["id"], offer["id"], 189.0, "amazon-nl", "https://a")
        self.assertEqual(self.store.status()["deals"], 1)

    def test_status_is_calm_above_the_target(self):
        watch, offer = self.watch_with_offer(target=99.0)
        self.store.record_price(watch["id"], offer["id"], 249.0, "amazon-nl", "https://a")
        self.assertEqual(self.store.status()["deals"], 0)

    def test_status_lists_broken_shops(self):
        watch, offer = self.watch_with_offer()
        self.store.record_failure(watch["id"], offer["id"], "403")
        broken = self.store.status()["broken"]
        self.assertEqual(broken[0]["shop"], "amazon-nl")

    def test_muted_watches_do_not_count_as_a_deal(self):
        watch, offer = self.watch_with_offer(target=199.0)
        self.store.update_watch(watch["id"], muted=True)
        self.store.record_price(watch["id"], offer["id"], 189.0, "amazon-nl", "https://a")
        self.assertEqual(self.store.status()["deals"], 0)


class TestSchema(StoreTestCase):
    def test_floor_column_exists_after_upgrade(self):
        columns = {row["name"] for row in self.store.db.execute("PRAGMA table_info(watches)")}
        self.assertIn("floor", columns)

    def test_old_database_keeps_its_rows(self):
        path = os.path.join(self.dir.name, "oud.db")
        import sqlite3

        old = sqlite3.connect(path)
        old.execute(
            "CREATE TABLE watches (id TEXT PRIMARY KEY, name TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',"
            " target REAL, threshold REAL, muted INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL,"
            " last_checked INTEGER, best_price REAL, best_shop TEXT, best_url TEXT, best_title TEXT,"
            " best_source TEXT, low_price REAL, low_at INTEGER)"
        )
        old.execute("PRAGMA user_version=1")
        old.execute("INSERT INTO watches (id, name, created) VALUES ('oud', 'Oude watch', 1)")
        old.commit()
        old.close()

        store = Store(path)
        self.assertEqual(store.watch("oud")["name"], "Oude watch")
        self.assertEqual(store.db.execute("PRAGMA user_version").fetchone()[0], 2)
        store.close()


class TestFormatting(unittest.TestCase):
    def test_money_is_dutch(self):
        self.assertEqual(money(1234.5), "€1.234,50")
        self.assertEqual(money(9.99), "€9,99")
        self.assertEqual(money(None), "-")

    def test_pct(self):
        self.assertEqual(pct(12.4), "12%")
        self.assertEqual(pct(None), "-")


if __name__ == "__main__":
    unittest.main()

class TestSoftNotes(unittest.TestCase):
    def test_soft_note_prefix(self):
        from pricewatch.store import SOFT_NOTE, is_soft

        self.assertTrue(is_soft(SOFT_NOTE + " €4,84 ligt onder je bodem"))
        self.assertFalse(is_soft("shop houdt ons tegen (403)"))
        self.assertFalse(is_soft(None))
        self.assertFalse(is_soft(""))
