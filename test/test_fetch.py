"""Wat er gebeurt als de verbinding hapert.

Een pricewatch draait wekenlang onaangeroerd. Een afgekapt antwoord, een proxy
die onzin teruggeeft of een winkel die met status 200 om een botcontrole vraagt
mag hooguit die ene winkel kosten: nooit een uitzondering die de hele ronde
meeneemt.
"""

import http.client
import json
import os
import ssl
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from pricewatch import fetch  # noqa: E402


class FakeHandle:
    """Net genoeg van een urllib-antwoord om fetch.get te laten werken."""

    def __init__(self, body=b"", encoding="", error=None, url="https://winkel.nl/product/1"):
        self.body = body
        self.error = error
        self.url = url
        self.status = 200
        self.headers = {"Content-Encoding": encoding} if encoding else {}

    def read(self):
        if self.error:
            raise self.error
        return self.body

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0

    def open(self, request, timeout=0):
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class FetchTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.real = (fetch._opener, fetch._COOLDOWN_FILE, fetch._throttle, fetch.time.sleep, fetch._loaded)
        fetch._COOLDOWN_FILE = Path(self.dir.name) / "cooldown.json"
        fetch._throttle = lambda host: None  # nooit echt wachten in een test
        fetch.time.sleep = lambda seconds: None
        fetch._loaded = True  # niet de cooldown van deze machine inlezen
        fetch._cooldown.clear()
        fetch._last_hit.clear()

    def tearDown(self):
        fetch._opener, fetch._COOLDOWN_FILE, fetch._throttle, fetch.time.sleep, fetch._loaded = self.real
        fetch._cooldown.clear()
        fetch._last_hit.clear()

    def net(self, outcome):
        opener = FakeOpener(outcome)
        fetch._opener = opener
        return opener


class TestHaperendeVerbinding(FetchTestCase):
    def test_afgekapt_antwoord_is_een_fetchfout(self):
        opener = self.net(FakeHandle(error=http.client.IncompleteRead(b"<html>", 40)))
        with self.assertRaises(fetch.FetchError) as cm:
            fetch.get("https://winkel.nl/product/1", retries=1)
        self.assertIn("afgebroken", str(cm.exception))
        self.assertEqual(opener.calls, 2, "een hapering mag één keer opnieuw")

    def test_onzin_statusregel_is_een_fetchfout(self):
        # Een proxy of een gevangen portal die geen http antwoordt.
        self.net(FakeHandle(error=http.client.BadStatusLine("GARBAGE")))
        with self.assertRaises(fetch.FetchError) as cm:
            fetch.get("https://winkel.nl/product/1", retries=0)
        self.assertIn("afgebroken", str(cm.exception))

    def test_ssl_fout_is_een_fetchfout(self):
        self.net(FakeHandle(error=ssl.SSLError("handshake failed")))
        with self.assertRaises(fetch.FetchError) as cm:
            fetch.get("https://winkel.nl/product/1", retries=0)
        self.assertIn("netwerkfout", str(cm.exception))

    def test_timeout_is_een_netwerkfout(self):
        self.net(FakeHandle(error=TimeoutError("timed out")))
        with self.assertRaises(fetch.FetchError) as cm:
            fetch.get("https://winkel.nl/product/1", retries=0)
        self.assertIn("timed out", str(cm.exception))

    def test_afgekapte_gzip_valt_terug_op_de_ruwe_bytes(self):
        body = "<html><title>Toiletpapier</title>&euro; 12,89</html>".encode()
        self.net(FakeHandle(body=body, encoding="gzip"))
        response = fetch.get("https://winkel.nl/product/1", retries=0)
        self.assertIn("12,89", response.body)


class TestBotcontrole(FetchTestCase):
    def test_botcontrole_met_status_200_is_blocked(self):
        # Cloudflare serveert zijn challenge met status 200. Zonder deze
        # controle heet dat "geen prijs gevonden" en loopt elke ronde opnieuw
        # tegen dezelfde muur.
        self.net(FakeHandle(body=b"<html><title>Just a moment...</title></html>"))
        with self.assertRaises(fetch.Blocked):
            fetch.get("https://winkel.nl/product/1", retries=0)
        self.assertIn("winkel.nl", fetch._cooldown, "de winkel hoort even op pauze")

    def test_gewone_pagina_met_captcha_script_is_geen_muur(self):
        # Winkels zetten reCAPTCHA en een cloudflare-cdn in hun gewone footer.
        body = (
            b'<html><head><script src="https://www.google.com/recaptcha/api.js"></script>'
            b'<link href="https://cdnjs.cloudflare.com/ajax/libs/x/x.css"></head>'
            b"<body>&euro; 99,00</body></html>"
        )
        self.net(FakeHandle(body=body))
        response = fetch.get("https://winkel.nl/product/1", retries=0)
        self.assertIn("99,00", response.body)


class TestCooldown(FetchTestCase):
    def test_retry_after_wordt_gelezen(self):
        self.assertEqual(fetch._retry_after({"Retry-After": "30"}), 30.0)
        self.assertEqual(fetch._retry_after({"Retry-After": "99999"}), 600.0)
        self.assertEqual(fetch._retry_after({}), 0.0)
        self.assertEqual(fetch._retry_after(None), 0.0)
        self.assertEqual(fetch._retry_after({"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}), 0.0)

    def test_retry_after_verlengt_de_pauze(self):
        fetch._cool_down("drukke-winkel.nl", extra=45)
        self.assertGreaterEqual(fetch._cooldown["drukke-winkel.nl"] - time.time(), 44)

    def test_pauze_van_een_winkel_raakt_een_andere_niet(self):
        until = time.time() + 5
        fetch._cooldown["coolblue:interval"] = 1.2
        fetch._cooldown["coolblue"] = until
        fetch._cool_down("amazon.nl", extra=300)
        state = json.loads(fetch._COOLDOWN_FILE.read_text())
        self.assertAlmostEqual(state["coolblue"]["until"], until, delta=1.5)
        self.assertGreater(state["amazon.nl"]["until"], time.time() + 100)

    def test_winkel_op_pauze_wordt_niet_gevraagd(self):
        # Wachten en het dan tóch nog eens vragen is precies wat een botmuur
        # wil voorkomen: 120 seconden hangen en dan alsnog 403.
        fetch._cool_down("winkel.nl", extra=600)
        opener = self.net(FakeHandle(body=b"<html></html>"))
        with self.assertRaises(fetch.Blocked) as cm:
            fetch.get("https://winkel.nl/product/1", retries=0)
        self.assertIn("pauze", str(cm.exception))
        self.assertEqual(opener.calls, 0, "een winkel die nee zei wordt niet nog eens gevraagd")

    def test_pauze_overleeft_een_nieuwe_run(self):
        fetch._cool_down("winkel.nl", extra=60)
        fetch._cooldown.clear()
        fetch._loaded = False
        fetch._load_cooldown()
        self.assertGreater(fetch._cooldown["winkel.nl"], time.time() + 50)


if __name__ == "__main__":
    unittest.main()
