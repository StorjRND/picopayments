import os
import json
import shutil
import unittest
import tempfile
from picopayments import srv
from picopayments import etc
from picopayments import rpc
from picopayments import lib
from picopayments import Client
from counterpartylib.lib.micropayments import util


rpc.CALL_LOCAL_PROCESS = True
CP_URL = "http://139.59.214.74:14000/api/"


class TestUsrClientBlockSend(unittest.TestCase):

    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="picopayments_test_")
        self.basedir = os.path.join(self.tempdir, "basedir")
        shutil.copytree("tests/fixtures", self.basedir)
        srv.main([
            "--testnet",
            "--basedir={0}".format(self.basedir),
            "--cp_url={0}".format(CP_URL)
        ], serve=False)
        with open(os.path.join(self.basedir, "data.json")) as fp:
            self.data = json.load(fp)

    def tearDown(self):
        shutil.rmtree(self.tempdir)

    def test_standard_usage(self):
        url = "https://127.0.0.1:15000/api/"
        client = Client(url, verify_ssl_cert=False)
        src_wif = self.data["funded_wifs"]["gamma"]
        dest_address = util.wif2address(util.random_wif(netcode=etc.netcode))
        quantity = 42
        asset = "A14456548018133352000"
        txid = client.block_send(
            source=src_wif, destination=dest_address, asset=asset,
            quantity=quantity, publish_tx=False
        )
        self.assertIsNotNone(txid)


if __name__ == "__main__":
    unittest.main()