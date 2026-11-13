"""Producten zoeken met een nepnetwerk.

Zoeken is de omgekeerde richting van checken: je geeft een term en krijgt
producten terug. Hier testen we wat er uit komt, niet wat er wordt opgeslagen.
"""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from pricewatch import fetch, search, shops  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures")

EEN_PRODUCT = """
<script type="application/ld+json">
{"@type":"Product","name":"Logitech MX Master 3S","offers":
 {"@type":"Offer","price":"74.00","url":"https://www.coolblue.nl/product/970149/mx-master-3s.html"}}
</script>
"""

TWEE_PRODUCTEN = """
<script type="application/ld+json">
{"@type":"Product","name":"Logitech MX Master 3S","offers":
 {"@type":"Offer","price":"74.00","url":"https://winkel.nl/mx-master-3s"}}
</script>
<script type="application/ld+json">
{"@type":"Product","name":"Logitech MX Master 3S voor Mac","offers":
 {"@type":"Offer","price":"69.00","url":"https://winkel.nl/mx-master-3s-mac"}}
</script>
<script type="application/ld+json">
{"@type":"Product","name":"Logitech MX Master 3S","offers":
 {"@type":"Offer","price":"74.00","url":"https://winkel.nl/mx-master-3s"}}
</script>
"""


class FakeNetwork:
    """Serves canned pages per url fragment; records every request."""

    def __init__(self, pages, errors=()):
        self.pages = pages
        self.errors = tuple(errors)
        self.calls = []

    def __call__(self, url, timeout=12.0, retries=2):
        self.calls.append(url)
        for fragment in self.errors:
            if fragment in url:
                raise fetch.FetchError(f"{fragment} weigert")
        for fragment, body in self.pages.items():
            if fragment in url:
                return fetch.Response(url=url, status=200, body=body)
        return fetch.Response(url=url, status=200, body="<html></html>")


class SearchTestCase(unittest.TestCase):
    def setUp(self):
        self.real_get = fetch.get
        self.real_sleep = search.time.sleep

    def tearDown(self):
        fetch.get = self.real_get
        search.time.sleep = self.real_sleep

    def net(self, pages, errors=()):
        fetch.get = FakeNetwork(pages, errors)
        return fetch.get


class TestShopIds(SearchTestCase):
    def test_standaard_is_de_bekende_winkels(self):
        self.assertEqual(search.shop_ids(""), list(search.DEFAULT_SHOPS))
        for shop_id in search.DEFAULT_SHOPS:
            self.assertIn(shop_id, shops.SHOPS)

    def test_explicit_lijst_met_spaties(self):
        self.assertEqual(search.shop_ids(" coolblue , amazon-nl "), ["coolblue", "amazon-nl"])

    def test_onbekende_winkel_stopt_met_naam(self):
        with self.assertRaises(ValueError) as cm:
            search.shop_ids("coolblue, bolcom")
        self.assertIn("bolcom", str(cm.exception))


class TestSearchShop(SearchTestCase):
    def test_json_ld_product_wordt_een_treffer(self):
        self.net({"coolblue": EEN_PRODUCT})
        result = search.search_shop("coolblue", "mx master 3s")
        self.assertEqual(result.error, "")
        self.assertEqual(len(result.hits), 1)
        hit = result.hits[0]
        self.assertEqual(hit.price, 74.0)
        self.assertEqual(hit.shop_name, "Coolblue")
        self.assertIn("mx-master-3s", hit.url)

    def test_meerdere_producten_en_dubbelen_weg(self):
        self.net({"coolblue": TWEE_PRODUCTEN})
        result = search.search_shop("coolblue", "mx master 3s")
        self.assertEqual(sorted(h.price for h in result.hits), [69.0, 74.0])
        self.assertEqual(len({h.url for h in result.hits}), 2)

    def test_winkel_die_weigert_landt_in_de_fout(self):
        net = self.net({}, errors=["amazon"])
        result = search.search_shop("amazon-nl", "mx master 3s")
        self.assertEqual(result.hits, [])
        self.assertIn("weigert", result.error)
        self.assertTrue(net.calls)

    def test_winkel_zonder_zoekadres(self):
        result = search.search_shop("onbekend-winkel", "mx master")
        self.assertEqual(result.hits, [])
        self.assertTrue(result.error)

    def test_per_shop_begrenst_de_lijst(self):
        body = "".join(
            f'<script type="application/ld+json">{{"@type":"Product","name":"MX Master 3S variant {i}",'
            f'"offers":{{"@type":"Offer","price":"{70 + i}.00","url":"https://winkel.nl/mx-{i}"}}}}</script>'
            for i in range(9)
        )
        self.net({"coolblue": body})
        result = search.search_shop("coolblue", "mx master 3s", per_shop=3)
        self.assertEqual(len(result.hits), 3)

    def test_bannerprijs_op_de_zoekpagina_wordt_geen_treffer(self):
        body = (
            "<html><head><title>'mx master 3s' resultaten | Winkel</title></head><body>"
            '<span>promo: "tot € 10,- inruilkorting"</span>'
            '<span>100 koffiecups voor € 20,-</span></body></html>'
        )
        self.net({"winkel": body})
        result = search.search_shop("coolblue", "mx master 3s")
        self.assertEqual(result.hits, [])
        self.assertTrue(result.note)
        for hit in result.hits:
            self.assertNotIn("/zoeken", hit.url)

    def test_andere_productsoort_blijft_buiten(self):
        # Een SD-kaartlezer deelt het woord "kaart" met een grafische kaart, maar
        # het is een ander product: die mag niet als treffer terugkomen.
        resultaat = search.search_shop("coolblue", "grafische kaart")
        for hit in resultaat.hits:
            self.assertNotIn("kaartlezer", hit.title.lower(), hit.title)
            self.assertNotIn("sd", hit.title.lower().split(), hit.title)

    def test_echte_amazon_fixture(self):
        with open(os.path.join(FIXTURES, "amazon-search.html"), encoding="utf-8") as fh:
            body = fh.read()
        self.net({"amazon": body})
        result = search.search_shop("amazon-nl", "usb c hub 7 poorts")
        self.assertEqual(result.error, "")
        self.assertTrue(result.hits, "de fixture zou treffers moeten geven")
        for hit in result.hits:
            self.assertGreater(hit.price, 0)
            self.assertTrue(hit.title)
            self.assertTrue(hit.url.startswith("http"))
            self.assertIn("/dp/", hit.url)


class TestAlleenZoeken(unittest.TestCase):
    """AliExpress levert prijzen op de zoekpagina, niet op de productpagina."""

    def test_aliexpress_is_geen_volgbare_winkel(self):
        self.assertTrue(shops.alleen_zoeken("aliexpress"))
        self.assertFalse(shops.zoek_adres("aliexpress"))

    def test_andere_winkels_blijven_volgbaar(self):
        for shop_id in search.DEFAULT_SHOPS:
            if shop_id != "aliexpress":
                self.assertTrue(shops.zoek_adres(shop_id), shop_id)

    def test_winkel_vertelt_waarom(self):
        self.assertIn("JavaScript", shops.SHOPS["aliexpress"].note)


class TestSkippedShops(unittest.TestCase):
    def test_overgeslagen_winkels_noemen_reden(self):
        lijst = search.skipped("")
        self.assertTrue(lijst, "een zoekopdracht hoort te vertellen wat er niet gevraagd is")
        for naam, reden in lijst:
            self.assertTrue(naam)
            self.assertTrue(reden, f"{naam} zonder reden")

    def test_winkel_die_wel_gevraagd_is_staat_niet_in_de_lijst(self):
        gevraagd = ",".join(search.SKIPPED_SHOPS)
        for naam, _ in search.skipped(gevraagd):
            self.assertNotIn(naam, [shops.label(s) for s in search.SKIPPED_SHOPS])

    def test_elke_overgeslagen_winkel_is_een_echte_winkel(self):
        for shop_id in search.SKIPPED_SHOPS:
            self.assertIn(shop_id, shops.SHOPS)
            self.assertTrue(shops.SHOPS[shop_id].note, f"{shop_id} moet zeggen waarom")


class TestSearchAll(SearchTestCase):
    def test_alle_winkels_en_sortering_op_prijs(self):
        self.net({"amazon": EEN_PRODUCT, "coolblue": TWEE_PRODUCTEN})
        search.time.sleep = lambda _: None
        results = search.search("mx master 3s", "coolblue,amazon-nl")
        self.assertEqual([r.shop for r in results], ["coolblue", "amazon-nl"])
        alle = search.hits(results)
        self.assertEqual(alle[0].price, 69.0)
        self.assertEqual([h.price for h in alle], sorted(h.price for h in alle))

    def test_voortgang_per_winkel(self):
        self.net({"winkel": EEN_PRODUCT})
        search.time.sleep = lambda _: None
        gezien = []
        search.search("mx master 3s", "coolblue", on_progress=gezien.append)
        self.assertEqual(len(gezien), 1)

    def test_json_vorm_voor_het_paneel(self):
        self.net({"coolblue": EEN_PRODUCT})
        search.time.sleep = lambda _: None
        results = search.search("mx master 3s", "coolblue")
        data = {
            "term": "mx master 3s",
            "shops": [r.as_dict() for r in results],
            "hits": [h.as_dict() for h in search.hits(results)],
        }
        self.assertEqual(data["term"], "mx master 3s")
        self.assertEqual(len(data["hits"]), 1)
        self.assertEqual(sorted(data["hits"][0]), ["note", "price", "shop", "shop_name", "source", "title", "url", "watchable"])

    def test_treffer_uit_zoek_winkel_kan_geen_watch_worden(self):
        self.net({"coolblue": EEN_PRODUCT})
        result = search.search_shop("coolblue", "mx master 3s")
        self.assertTrue(result.hits[0].watchable)

    def test_aliexpress_treffer_is_niet_volgbaar(self):
        body = (
            '<a class="us--container search-card-item" href="//nl.aliexpress.com/item/1.html">'
            '<div aria-label="Toiletpapier 36 rollen" role="heading"><h3>Toiletpapier 36 rollen</h3></div>'
            '<div class="us--price--3al65BO" aria-label="€12,89"></div></a>'
        )
        self.net({"aliexpress": body})
        result = search.search_shop("aliexpress", "toiletpapier")
        self.assertTrue(result.hits)
        self.assertFalse(result.hits[0].watchable, "zo'n adres blijft leeg bij elke check")
        self.assertFalse(result.as_dict()["watchable"])


class TestSearchCommand(SearchTestCase):
    """Het CLI-commando zelf, met uitvoer opgevangen."""

    def run_zoek(self, argv):
        import io
        from contextlib import redirect_stdout
        from pricewatch import cli
        from pricewatch.store import Store

        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        store = Store(os.path.join(self.dir.name, "pricewatch.db"))
        buf = io.StringIO()
        args = cli.build_parser().parse_args(argv)
        self.addCleanup(store.close)
        with redirect_stdout(buf):
            code = args.func(store, args)
        return code, buf.getvalue()

    def test_tekstuitvoer_noemt_winkel_en_treffer(self):
        self.net({"coolblue": EEN_PRODUCT})
        search.time.sleep = lambda _: None
        code, uit = self.run_zoek(["zoek", "mx master 3s", "--shops", "coolblue"])
        self.assertEqual(code, 0)
        self.assertIn("Coolblue", uit)
        self.assertIn("74,00", uit)
        self.assertIn("toevoegen met", uit)

    def test_json_uitvoer_is_parseerbaar(self):
        import json

        self.net({"coolblue": EEN_PRODUCT})
        search.time.sleep = lambda _: None
        code, uit = self.run_zoek(["zoek", "mx master 3s", "--shops", "coolblue", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(uit)
        self.assertEqual(data["term"], "mx master 3s")
        self.assertEqual(data["hits"][0]["price"], 74.0)

    def test_alias_search(self):
        self.net({"coolblue": EEN_PRODUCT})
        search.time.sleep = lambda _: None
        code, uit = self.run_zoek(["search", "mx master 3s", "--shops", "coolblue"])
        self.assertEqual(code, 0)
        self.assertIn("Coolblue", uit)

    def test_onbekende_winkel_stopt_met_foutcode(self):
        with self.assertRaises(SystemExit):
            self.run_zoek(["zoek", "mx master", "--shops", "bolcom"])


if __name__ == "__main__":
    unittest.main()
