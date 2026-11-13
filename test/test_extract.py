"""Prijsextractie, getest tegen opgeslagen pagina's van echte winkels.

De fixtures zijn volledige zoekpagina's (amazon, proshop, apple), zodat een
wijziging in de markup van een winkel hier zichtbaar wordt voordat hij in een
wachtperiode van drie maanden zichtbaar wordt.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from pricewatch import extract, shops  # noqa: E402


def fixture(name: str) -> str:
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8", errors="replace") as handle:
        return handle.read()


class TestParsePrice(unittest.TestCase):
    def test_dutch_formats(self):
        cases = {
            "€ 1.234,56": 1234.56,
            "1.234,56": 1234.56,
            "€1299,-": 1299.0,
            "1299,-": 1299.0,
            "€ 12,99": 12.99,
            "39,95 €": 39.95,
            "EUR 1 234,00": 1234.0,
            "1.234": 1234.0,
        }
        for text, want in cases.items():
            self.assertEqual(extract.parse_price(text), want, text)

    def test_rejects_nonsense(self):
        for text in ("", None, "gratis", "0", "€ 0,00", "-5"):
            self.assertIsNone(extract.parse_price(text), repr(text))

    def test_numbers(self):
        self.assertEqual(extract.parse_price(2499), 2499.0)
        self.assertEqual(extract.parse_price("2499.99"), 2499.99)


class TestTokens(unittest.TestCase):
    def test_keeps_model_numbers(self):
        self.assertEqual(extract.tokenize("AirPods Pro 2"), {"airpods", "pro", "2"})

    def test_ordinals_fold_to_a_digit(self):
        for text in ("AirPods Pro (2e generatie)", "AirPods Pro 2nd", "AirPods Pro 2"):
            self.assertIn("2", extract.tokenize(text), text)

    def test_drops_stopwords(self):
        self.assertNotIn("van", extract.tokenize("case van de macbook"))

    def test_match_score(self):
        self.assertEqual(extract.match_score("airpods pro 2", "Apple AirPods Pro 2 USB-C"), 1.0)
        self.assertAlmostEqual(extract.match_score("airpods pro 2", "Apple AirPods Pro 1"), 0.667, places=3)
        self.assertEqual(extract.match_score("airpods pro 2", "Hoesje voor oordopjes"), 0.0)

    def test_accessory_words(self):
        found = extract.accessory_score("AirPods Pro 2", "Siliconen hoesje voor AirPods Pro 2")
        self.assertIn("hoesje", found)

    def test_oordophaken_en_vleugels(self):
        # Een oorhaakje noemt het product woord voor woord en kost een tiende.
        self.assertIn("hooks", extract.accessory_score("airpods pro 2", "elago Ear Hooks for AirPods Pro 3/Pro 2"))
        self.assertIn("wing", extract.accessory_score("airpods pro 2", "AirPods Pro 2 Wing Tips"))
        self.assertEqual(
            extract.accessory_score("airpods pro 2", "Apple AirPods Pro (2e generatie) met MagSafe-oplaadcase"), []
        )

    def test_synoniem_vindt_de_ander_wording(self):
        # "toiletpapier" is een synoniem van "wc"; het losse woord "papier" staat
        # er niet in, want dat zou ook "fotopapier" binnenhalen.
        self.assertEqual(extract.match_score("wc papier", "3-laags Toiletpapier 36 Rollen"), 0.5)
        self.assertEqual(extract.match_score("wc papier", "Papier toilet rollen"), 1.0)

    def test_gevaarlijk_korte_woorden_meten_exact_staan(self):
        # "wc" is een substring van bijna elk Engels woord, dus geen losse match.
        self.assertFalse(extract.word_hit("wc", extract.tokenize("Woodcock Deluxe")))

    def test_gespleten_woorden_tellen_mee(self):
        # Nederlands plakt woorden aan elkaar: "papier" staat niet in "papieren".
        self.assertTrue(extract.word_hit("papier", extract.tokenize("papieren")))
        self.assertTrue(extract.word_hit("papier", extract.tokenize("gekleurd papier 500 vellen")))

    def test_gespleten_woorden_met_andere_betekenis_blijven_buiten(self):
        # "fotopapier" eindigt op papier maar is een ander product dan wc-papier.
        self.assertFalse(extract.word_hit("papier", extract.tokenize("HP fotopapier glanzend")))
        self.assertFalse(extract.word_hit("papier", extract.tokenize("rollenpapier")))

    def test_synoniemen_verruimen_niet_alles(self):
        self.assertEqual(extract.match_score("wc papier", "Stofwrijver elektrostatisch"), 0.0)

    def test_sd_kaartlezer_is_geen_grafische_kaart(self):
        # Beide titels bevatten "kaart", maar het zijn andere producten.
        self.assertEqual(extract.conflicting("grafische kaart", "USB-C-naar-SD-kaartlezer"), ["sd"])
        self.assertEqual(extract.conflicting("grafische kaart", "SD-kaart 128GB"), ["sd"])
        self.assertEqual(extract.conflicting("grafische kaart", "MAXSUN Grafische kaarten GeForce GT 730 4 GB"), [])
        self.assertEqual(extract.conflicting("grafische kaart", "SOYO NVIDIA GT 740 4GB grafische kaart GDDR3"), [])

    def test_echte_sd_kaart_blijft_zoekbaar(self):
        self.assertEqual(extract.conflicting("sd kaart 128gb", "SanDisk Extreme SD-kaart 128GB"), [])
        self.assertEqual(extract.conflicting("sd kaart 128gb", "USB-C-naar-SD-kaartlezer"), [])
        self.assertEqual(extract.accessory_score("Silicone hoes", "Silicone hoes voor AirPods"), [])

    def test_plain_title_strips_ad_label(self):
        self.assertEqual(
            extract.plain_title("Gesponsorde advertentie - Anker USB-C Hub"), "Anker USB-C Hub"
        )


class TestJsonLd(unittest.TestCase):
    def test_product_page(self):
        html = """<html><head><title>Coolblue</title>
        <script type="application/ld+json">
        {"@type":"Product","name":"Sitecom USB-C naar USB-C Hub",
         "offers":{"@type":"Offer","price":"54.00","priceCurrency":"EUR","availability":"http://schema.org/InStock"}}
        </script></head><body></body></html>"""
        found = extract.scan(html, "https://example.nl/p").candidates
        self.assertEqual(found[0].price, 54.0)
        self.assertEqual(found[0].source, extract.CONF_JSONLD)
        self.assertTrue(found[0].available)
        self.assertEqual(extract.scan(html).best().price, 54.0)

    def test_out_of_stock_is_not_the_price_of_record(self):
        html = """<script type="application/ld+json">
        {"@type":"Product","name":"Hub","offers":{"price":"19.00","priceCurrency":"EUR",
         "availability":"http://schema.org/OutOfStock"}}
        </script>"""
        self.assertIsNone(shops.product_price(html, "https://example.nl/p"))

    def test_graph_and_list_shapes(self):
        html = """<script type="application/ld+json">
        {"@graph":[{"@type":["Product"],"name":"A","offers":[{"price":"10.00"}]}]}
        </script>"""
        self.assertEqual(extract.scan(html).candidates[0].price, 10.0)

    def test_pricespecification(self):
        html = """<script type="application/ld+json">
        {"@type":"Product","name":"A","offers":{"priceSpecification":{"price":88.5,"priceCurrency":"EUR"}}}
        </script>"""
        self.assertEqual(extract.scan(html).candidates[0].price, 88.5)


class TestMetaAndMicrodata(unittest.TestCase):
    def test_product_meta(self):
        html = '<html><head><meta property="product:price:amount" content="249,00"></head></html>'
        found = extract.scan(html).candidates
        self.assertEqual(found[0].price, 249.0)
        self.assertEqual(found[0].source, extract.CONF_META)

    def test_microdata(self):
        html = '<div itemscope><span itemprop="price" content="79.99">79.99</span></div>'
        self.assertEqual(extract.scan(html).candidates[0].price, 79.99)

    def test_shipping_meta_is_not_a_price(self):
        html = '<meta property="product:shipping_price" content="5.00">'
        self.assertEqual(extract.scan(html).candidates, [])


class TestInlineState(unittest.TestCase):
    def test_balanced_object_reads_past_strings(self):
        import json

        html = 'window.x = {"a":"}{","b":{"price":"12,50"}};'
        start = extract.ASSIGN_RE.search(html).end()
        self.assertEqual(json.loads(extract._balanced_object(html, start)), {"a": "}{", "b": {"price": "12,50"}})

    def test_price_object_borrows_name_from_parent(self):
        html = (
            'window.pageLevelData = {"value":{"title":"Belkin Connect Thunderbolt 4 Core Hub",'
            '"url":"/nl/shop/product/hr7s2z/a/belkin","productPrice":{"priceCurrent":"€ 199,95",'
            '"priceCurrency":"EUR"}}};'
        )
        found = extract._inline_state(html)
        self.assertEqual(found[0].price, 199.95)
        self.assertIn("Belkin", found[0].title)

    def test_other_currency_is_ignored(self):
        html = 'window.x = {"title":"Foreign","price":"10.00","currency":"USD"};'
        self.assertEqual(extract._inline_state(html), [])


class TestShopListings(unittest.TestCase):
    def test_amazon_pairs_title_price_and_link(self):
        listing = shops.search_listing(
            fixture("amazon-search.html"), "https://www.amazon.nl/s?k=usb-c-hub", "amazon-nl", "usb c hub 7 poorts"
        )
        self.assertTrue(listing.candidates, listing.note)
        best = listing.candidates[0]
        self.assertTrue(best.title)
        self.assertIn("/dp/", best.url)
        for candidate in listing.candidates:
            self.assertIn("hub", candidate.title.lower())

    def test_accessories_do_not_win(self):
        listing = shops.search_listing(
            fixture("amazon-search.html"), "https://www.amazon.nl/s?k=usb-c-hub", "amazon-nl", "usb c hub 7 poorts"
        )
        prices = [c.price for c in listing.candidates]
        self.assertGreater(len(prices), 1)
        self.assertGreater(max(prices) / min(prices), 1.5)

    def test_apple_listing(self):
        listing = shops.search_listing(
            fixture("apple-search.html"), "https://www.apple.com/nl/search/x", "apple", "usb c hub thunderbolt 4"
        )
        titles = " ".join(c.title.lower() for c in listing.candidates)
        self.assertIn("hub", titles)
        self.assertTrue(all(c.price > 50 for c in listing.candidates))

    def test_proshop_listing_has_titles_and_prices(self):
        listing = shops.search_listing(
            fixture("proshop-search.html"), "https://www.proshop.nl/Search?q=x", "proshop", "usb c hub"
        )
        for candidate in listing.candidates:
            self.assertTrue(candidate.title)
            self.assertGreater(candidate.price, 0)

    def test_page_without_products_says_so(self):
        listing = shops.search_listing("<html><body>niets</body></html>", "https://x.nl", "coolblue", "usb c hub")
        self.assertFalse(listing)
        self.assertTrue(listing.note)

    def test_bannerprijzen_op_een_zoekpagina_zijn_geen_producten(self):
        # De paginatitel van een zoekpagina is de zoekterm, dus een prijs uit de
        # paginatek matcht de zoekterm volledig. Zo'n prijs hoort bij een banner
        # en niet bij een product.
        listing = shops.search_listing(
            fixture("coolblue-search.html"),
            "https://www.coolblue.nl/zoeken?query=cortina+esoma+handvaten",
            "coolblue",
            "cortina esoma handvaten",
        )
        self.assertFalse(listing, [c.price for c in listing.candidates])
        self.assertTrue(listing.note)

    def test_aliexpress_koppelt_titel_prijs_en_link(self):
        listing = shops.search_listing(
            fixture("aliexpress-search.html"),
            "https://nl.aliexpress.com/w/wholesale-wc+papier.html",
            "aliexpress",
            "wc papier",
        )
        self.assertTrue(listing.candidates, listing.note)
        for candidate in listing.candidates:
            self.assertTrue(candidate.title, "elke treffer hoort een titel te hebben")
            self.assertGreater(candidate.price, 0)
            self.assertIn("/item/", candidate.url)
        prijzen = sorted(c.price for c in listing.candidates)
        self.assertEqual(len(set(prijzen)), len(prijzen), "prijzen van verschillende kaarten mogen niet door elkaar lopen")

    def test_aliexpress_kaart_zonder_prijs_wordt_genegeerd(self):
        kaart = (
            '<a class="us--container search-card-item" href="//nl.aliexpress.com/item/1.html">'
            '<div aria-label="Sokken" role="heading"><h3>Sokken</h3></div></a>'
            '<a class="us--container search-card-item" href="//nl.aliexpress.com/item/2.html">'
            '<div aria-label="Handdoek" role="heading"><h3>Handdoek</h3></div>'
            '<div class="us--price--3al65BO" aria-label="€12,50"></div></a>'
        )
        gevonden = shops._aliexpress(kaart, "https://nl.aliexpress.com/")
        self.assertEqual([(c.price, c.title) for c in gevonden], [(12.5, "Handdoek")])

    def test_kandidaat_zonder_productadres_kan_geen_treffer_worden(self):
        html = """<script type="application/ld+json">
        {"@type":"Product","name":"MX Master 3S","offers":
         {"@type":"Offer","price":"74.00"}}</script>"""
        listing = shops.search_listing(html, "https://winkel.nl/zoeken", "coolblue", "mx master 3s")
        self.assertFalse(listing)


class TestNieuweWinkels(unittest.TestCase):
    def test_informatique_koppelt_titel_prijs_en_link(self):
        listing = shops.search_listing(
            fixture("informatique-search.html"),
            "https://www.informatique.nl/zoeken?q=usb-c+hub",
            "informatique",
            "usb-c hub",
        )
        self.assertTrue(listing.candidates, listing.note)
        found = {candidate.title: candidate for candidate in listing.candidates}
        self.assertIn("StarTech 4-Port USB-C Hub met 100W PD 5Gbps", found)
        self.assertEqual(found["StarTech 4-Port USB-C Hub met 100W PD 5Gbps"].price, 61.0)
        self.assertIn("informatique.nl/244049/", found["StarTech 4-Port USB-C Hub met 100W PD 5Gbps"].url)
        self.assertNotIn("USB-C Hub zonder prijs", found, "een kaart zonder prijs is geen treffer")

    def test_informatique_laat_goedkope_accessoires_links_liggen(self):
        listing = shops.search_listing(
            fixture("informatique-search.html"),
            "https://www.informatique.nl/zoeken?q=usb-c+hub+7+poorten",
            "informatique",
            "usb-c hub 7 poorten",
        )
        self.assertTrue(listing.candidates, listing.note)
        prices = [candidate.price for candidate in listing.candidates]
        self.assertNotIn(4.5, prices, "een hoesje is niet de hub")
        self.assertEqual(listing.candidates[0].price, 29.95)

    def test_informatique_productpagina_leest_de_json_ld(self):
        candidate = shops.product_price(
            fixture("informatique-product.html"),
            "https://www.informatique.nl/244049/startech-4-port-usb-c-hub-100w-pd.html",
            "startech usb-c hub",
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.price, 61.0)
        self.assertIn("StarTech", candidate.title)
        self.assertTrue(candidate.trusted)

    def test_tweakers_zoekkaart_gebruikt_de_laagste_winkelprijs(self):
        listing = shops.search_listing(
            fixture("tweakers-search.html"),
            "https://tweakers.net/pricewatch/zoeken/?keyword=airpods+pro+3",
            "tweakers",
            "airpods pro 3",
        )
        self.assertTrue(listing.candidates, listing.note)
        found = {candidate.title: candidate for candidate in listing.candidates}
        self.assertEqual(found["Apple AirPods Pro 3 Wit"].price, 211.0, "de adviesprijs telt niet mee")
        self.assertIn("/pricewatch/2254416/", found["Apple AirPods Pro 3 Wit"].url)
        self.assertFalse([c for c in listing.candidates if "tweedehands" in c.title.lower()])
        self.assertTrue(all("/pricewatch/" in candidate.url for candidate in listing.candidates))

    def test_tweakers_productpagina_pakt_de_laagste_nieuwe_prijs(self):
        url = "https://tweakers.net/pricewatch/2254416/apple-airpods-pro-3-wit.html"
        candidate = shops.product_price(fixture("tweakers-product.html"), url, "airpods pro 3")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.price, 211.0)
        self.assertIn("AirPods Pro 3", candidate.title)
        self.assertIn("Amazon.nl", candidate.title)
        self.assertEqual(candidate.url, url, "de clickout-link verloopt; bewaar de pricewatch-pagina")
        self.assertNotEqual(candidate.price, 201.98, "refurbished is een ander product")


class TestMatching(unittest.TestCase):
    def _page(self, entries):
        rows = "".join(
            f'<div class="site-productlist-item site-customerCenterCard"><a title="{t}" href="/p/{i}">'
            f'<img alt="{t}"/></a><a class="site-product-link" href="/p/{i}"><h2>{t}</h2></a>'
            f'<div class="priceContainer"><span class="site-currency-lg">{p} \u20ac</span>'
            f'<span class="hidden-xs">(99,00 \u20ac)</span></div></div>'
            for i, (t, p) in enumerate(entries)
        )
        return f"<html><body>{rows}</body></html>"

    def test_common_words_do_not_count(self):
        page = self._page(
            [
                ("USB-C hub 4 poorts", "12,00"),
                ("USB-C hub 7 poorts", "25,00"),
                ("USB-C hub 10 poorts", "35,00"),
                ("USB-C kabel 2 meter", "8,00"),
                ("USB-C adapter", "15,00"),
            ]
        )
        listing = shops.search_listing(page, "https://x.nl", "proshop", "usb c hub 7 poorts")
        self.assertTrue(listing.candidates)
        self.assertEqual(listing.candidates[0].price, 25.0)

    def test_accessory_at_one_quarter_of_the_page_is_dropped(self):
        page = self._page(
            [
                ("AirPods Pro 2 USB-C", "249,00"),
                ("AirPods Pro 2 USB-C oplader", "229,00"),
                ("AirPods Pro 2 USB-C kabelset", "239,00"),
                ("AirPods Pro 2 USB-C beschermhoes", "19,00"),
                ("AirPods Pro 2 USB-C screenfolie", "14,00"),
                ("AirPods Pro 2 USB-C doosje", "24,00"),
            ]
        )
        listing = shops.search_listing(page, "https://x.nl", "proshop", "airpods pro 2")
        self.assertTrue(listing.candidates)
        # De oplader en de kabelset noemen hun Product, maar zijn een oplader en
        # een kabelset; alleen de AirPods zelf blijven over.
        self.assertEqual(min(c.price for c in listing.candidates), 249.0)
        self.assertTrue(all("accessoire" in c.title or "AirPods" in c.title for c in listing.candidates))
        self.assertEqual([c.title for c in listing.candidates], ["AirPods Pro 2 USB-C"])

    def test_alleen_accessoires_is_geen_treffer(self):
        # Een pagina met enkel hoesjes en doosjes levert geen prijs, want het enige
        # wat er staat is niet het product zelf.
        page = self._page(
            [
                ("Opbergdoos voor Logitech MX Master 3S", "5,09"),
                ("Harde muishoes voor Logitech MX Master 3S", "7,99"),
                ("Muisbehuizing voor Logitech MX Master 3S", "13,19"),
            ]
        )
        listing = shops.search_listing(page, "https://x.nl", "proshop", "logitech mx master 3s")
        self.assertFalse(listing)
        self.assertIn("accessoires", listing.note)

    def test_samenstelling_als_accessoire(self):
        # "muishoes" is een hoes en "opbergdoos" een doos; beide schrijven hun
        # woord aan elkaar, maar het blijft een accessoire.
        self.assertIn("hoes", extract.accessory_score("MX Master 3S", "Harde muishoes voor MX Master 3S"))
        self.assertIn("doos", extract.accessory_score("MX Master 3S", "Opbergdoos voor MX Master 3S"))
        self.assertEqual(extract.accessory_score("MX Master 3S", "Logitech MX Master 3S draadloze muis"), [])


class TestProductPages(unittest.TestCase):
    def test_absolute_link_is_kept(self):
        html = """<script type="application/ld+json">
        {"@type":"Product","name":"Hub","url":"/product/x",
         "offers":{"price":"31,00","priceCurrency":"EUR"}}</script>"""
        best = shops.product_price(html, "https://winkel.nl/p/1", "hub")
        self.assertEqual(best.url, "https://winkel.nl/product/x")

    def test_zoekpagina_levert_geen_regex_prijs(self):
        # Een watch die een zoekpagina volgt (zoals de oude banner-fout opleverde)
        # moet niets teruggeven, geen bannerbedrag.
        html = (
            "<html><head><title>'wc papier' resultaten | Coolblue</title></head>"
            "<body><span>tot &euro; 10,- inruilkorting</span>"
            "<span>100 koffiecups voor &euro; 20,-</span></body></html>"
        )
        self.assertIsNone(
            shops.product_price(html, "https://www.coolblue.nl/zoeken?query=wc+papier", "wc papier")
        )

    def test_productpagina_mag_wel_regex_gebruiken(self):
        # Op een productpagina is de paginatitel het product zelf, dus een prijs
        # uit de paginatek is hier het laatste hulpmiddel.
        html = "<html><head><title>Toiletpapier 36 rollen</title></head><body>&euro; 12,89</body></html>"
        best = shops.product_price(html, "https://nl.aliexpress.com/item/1.html", "toiletpapier")
        self.assertIsNotNone(best)
        self.assertEqual(best.price, 12.89)

    def test_herkent_zoekadressen(self):
        self.assertTrue(shops.is_search_url("https://www.coolblue.nl/zoeken?query=wc+papier"))
        self.assertTrue(shops.is_search_url("https://www.amazon.nl/s?k=wc+papier"))
        self.assertFalse(shops.is_search_url("https://www.coolblue.nl/product/970149/muis.html"))

    def test_eenheidsprijs_is_geen_productprijs(self):
        # Amazon zet in een aanbevelingsrij zowel €11,48 totaal als €1,15 per rol.
        # De goedkoopste winnen zou het product een tiende van zijn prijs geven.
        html = (
            "<html><head><title>Hakle Toiletpapier 20 rollen</title></head><body>"
            "<span>&euro; 11,48</span><span>&euro; 11 , 48</span><span>&euro; 11,48</span>"
            "<span>&euro; 11,48</span><span>&euro; 11,48</span><span>&euro; 11,48</span>"
            "<span>&euro; 11,48</span><span>(&euro; 1,15/eenheid)</span>"
            "</body></html>"
        )
        best = shops.product_price(html, "https://www.amazon.nl/dp/B00S1DHM5Y", "hakle toiletpapier")
        self.assertEqual(best.price, 11.48)

    def test_herhaling_wint_van_goedkoopste(self):
        html = (
            "<html><head><title>Product</title></head><body>"
            "&euro; 99,00 &euro; 99,00 &euro; 99,00 <span>&euro; 4,50</span>"
            "</body></html>"
        )
        best = shops.product_price(html, "https://winkel.nl/product/1", "product")
        self.assertEqual(best.price, 99.0)


if __name__ == "__main__":
    unittest.main()

class TestFollowProductLinks(unittest.TestCase):
    """Zoekpagina's die doorsturen naar een categoriepagina."""

    CATEGORY = """
    <div class="category">
      <a href="/product/970149/logitech-mx-master-3s.html">Logitech MX Master 3S draadloos</a>
      <a href="/product/2/apple-airpods-pro-2.html">Apple AirPods Pro 2 draadloos oordopjes</a>
      <a href="/product/9/usb-c-hub.html">Anker USB-C hub 7 in 1</a>
      <a href="/zoeken?query=x">terug naar zoeken</a>
      <a href="https://www.ander.nl/product/1/x.html">AirPods Pro 2 bij een andere winkel</a>
      <a href="/product/3/leeg.html">   </a>
    </div>
    """

    def test_best_match_first(self):
        links = shops.follow_product_links(self.CATEGORY, "https://www.coolblue.nl/zoeken?query=x", "coolblue", "airpods pro 2")
        self.assertEqual(links, ["https://www.coolblue.nl/product/2/apple-airpods-pro-2.html"])

    def test_other_domain_is_skipped(self):
        links = shops.follow_product_links(self.CATEGORY, "https://www.coolblue.nl/zoeken?query=x", "coolblue", "airpods pro 2")
        self.assertFalse([u for u in links if "ander.nl" in u])

    def test_search_link_is_not_a_product(self):
        links = shops.follow_product_links(self.CATEGORY, "https://www.coolblue.nl/zoeken?query=x", "coolblue", "usb-c hub")
        self.assertEqual(links, ["https://www.coolblue.nl/product/9/usb-c-hub.html"])

    def test_limit(self):
        html = "".join(
            f'<a href="/product/{i}/airpods-pro-{i}.html">Apple AirPods Pro {i}</a>' for i in range(10)
        )
        self.assertEqual(len(shops.follow_product_links(html, "https://w.nl/", "coolblue", "airpods pro", limit=3)), 3)

    def test_duplicate_link_is_kept_once(self):
        html = '<a href="/product/1/a.html">AirPods Pro 2</a><a href="/product/1/a.html">AirPods Pro 2</a>'
        self.assertEqual(len(shops.follow_product_links(html, "https://w.nl/", "coolblue", "airpods pro 2")), 1)

    def test_no_match_gives_nothing(self):
        self.assertEqual(shops.follow_product_links(self.CATEGORY, "https://www.coolblue.nl/", "coolblue", "stofzuiger"), [])
