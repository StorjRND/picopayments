# coding: utf-8
# Copyright (c) 2016 Fabian Barkhau <fabian.barkhau@gmail.com>
# License: MIT (see LICENSE file)


import os
import pycoin
from picopayments import validate
from picopayments import util
from picopayments.scripts import get_deposit_spend_secret_hash
from picopayments.scripts import get_deposit_payee_pubkey
from picopayments.scripts import get_commit_spend_secret_hash
from picopayments.scripts import get_commit_payee_pubkey
from picopayments.scripts import get_commit_revoke_secret_hash
from picopayments.scripts import get_commit_delay_time
from picopayments.channel.base import Base


class Payee(Base):

    def setup(self, payee_wif):
        with self.mutex:
            self.clear()
            self.payee_wif = payee_wif
            payee_pubkey = util.wif2pubkey(self.payee_wif)
            secret = os.urandom(32)  # secure random number
            self.spend_secret = util.b2h(secret)
            spend_secret_hash = util.b2h(util.hash160(secret))
            return payee_pubkey, spend_secret_hash

    def _validate_deposit_spend_secret_hash(self, script):
        given_spend_secret_hash = get_deposit_spend_secret_hash(script)
        own_spend_secret_hash = util.hash160hex(self.spend_secret)
        if given_spend_secret_hash != own_spend_secret_hash:
            msg = "Incorrect spend secret hash: {0} != {1}"
            raise ValueError(msg.format(
                given_spend_secret_hash, own_spend_secret_hash
            ))

    def _validate_deposit_payee_pubkey(self, script):
        given_payee_pubkey = get_deposit_payee_pubkey(script)
        own_payee_pubkey = util.wif2pubkey(self.payee_wif)
        if given_payee_pubkey != own_payee_pubkey:
            msg = "Incorrect payee pubkey: {0} != {1}"
            raise ValueError(msg.format(
                given_payee_pubkey, own_payee_pubkey
            ))

    def _assert_unopen_state(self):
        assert(self.payer_wif is None)
        assert(self.payee_wif is not None)
        assert(self.spend_secret is not None)
        assert(self.deposit_rawtx is None)
        assert(self.deposit_script_hex is None)
        assert(len(self.commits_active) == 0)
        assert(len(self.commits_revoked) == 0)

    def _validate_payer_deposit(self, rawtx, script_hex):
        tx = pycoin.tx.Tx.from_hex(rawtx)
        assert(tx.bad_signature_count() == 1)

        # TODO validate script
        # TODO check given script and rawtx match
        # TODO check given script is deposit script

    def _validate_payer_commit(self, rawtx, script_hex):
        tx = pycoin.tx.Tx.from_hex(rawtx)
        assert(tx.bad_signature_count() == 1)

        # TODO validate script
        # TODO validate rawtx signed by payer
        # TODO check it is for the current deposit
        # TODO check given script and rawtx match
        # TODO check given script is commit script

    def set_deposit(self, rawtx, script_hex):
        with self.mutex:
            self._assert_unopen_state()
            self._validate_payer_deposit(rawtx, script_hex)

            script = util.h2b(script_hex)
            self._validate_deposit_spend_secret_hash(script)
            self._validate_deposit_payee_pubkey(script)
            self.deposit_rawtx = rawtx
            self.deposit_script_hex = script_hex

    def request_commit(self, quantity):
        with self.mutex:
            self._validate_transfer_quantity(quantity)
            secret = util.b2h(os.urandom(32))  # secure random number
            secret_hash = util.hash160hex(secret)
            self.commits_requested.append(secret)
            return quantity, secret_hash

    def _validate_commit_secret_hash(self, script):
        given_spend_secret_hash = get_commit_spend_secret_hash(script)
        own_spend_secret_hash = util.hash160hex(self.spend_secret)
        if given_spend_secret_hash != own_spend_secret_hash:
            msg = "Incorrect spend secret hash: {0} != {1}"
            raise ValueError(msg.format(
                given_spend_secret_hash, own_spend_secret_hash
            ))

    def _validate_commit_payee_pubkey(self, script):
        given_payee_pubkey = get_commit_payee_pubkey(script)
        own_payee_pubkey = util.wif2pubkey(self.payee_wif)
        if given_payee_pubkey != own_payee_pubkey:
            msg = "Incorrect payee pubkey: {0} != {1}"
            raise ValueError(msg.format(
                given_payee_pubkey, own_payee_pubkey
            ))

    def set_commit(self, rawtx, script_hex):
        with self.mutex:
            self._validate_payer_commit(rawtx, script_hex)

            script = util.h2b(script_hex)
            self._validate_commit_secret_hash(script)
            self._validate_commit_payee_pubkey(script)

            revoke_secret_hash = get_commit_revoke_secret_hash(script)
            for revoke_secret in self.commits_requested[:]:

                # revoke secret hash must match as it would
                # otherwise break the channels reversability
                if revoke_secret_hash == util.hash160hex(revoke_secret):

                    # remove from requests
                    self.commits_requested.remove(revoke_secret)

                    # add to active
                    self._order_active()
                    self.commits_active.append({
                        "rawtx": rawtx, "script": script_hex,
                        "revoke_secret": revoke_secret
                    })
                    return self.get_transferred_amount()

            return None

    def revoke_until(self, quantity):
        with self.mutex:
            secrets = []
            self._order_active()
            for commit in reversed(self.commits_active[:]):
                if quantity < self.control.get_quantity(commit["rawtx"]):
                    secrets.append(commit["revoke_secret"])
                else:
                    break
            self.revoke_all(secrets)
            return secrets

    def close_channel(self):
        with self.mutex:
            assert(len(self.commits_active) > 0)
            self._order_active()
            commit = self.commits_active[-1]
            rawtx = self.control.finalize_commit(
                self.payee_wif, commit["rawtx"],
                util.h2b(self.deposit_script_hex)
            )
            commit["rawtx"] = rawtx  # update commit
            return util.gettxid(rawtx)

    def update(self):
        with self.mutex:

            # payout recoverable commits
            scripts = self.get_payout_recoverable()
            if len(scripts) > 0:
                self.payout_recover(scripts)

    def get_payout_recoverable(self):
        with self.mutex:
            scripts = []
            for commit in self.commits_active + self.commits_revoked:
                script = util.h2b(commit["script"])
                delay_time = get_commit_delay_time(script)
                address = util.script2address(
                    script, netcode=self.control.netcode
                )
                if self._commit_spent(commit):
                    continue
                if self.control.can_spend_from_address(address):
                    utxos = self.control.btctxstore.retrieve_utxos([address])
                    for utxo in utxos:
                        txid = utxo["txid"]
                        confirms = self.control.btctxstore.confirms(txid)
                        if confirms >= delay_time:
                            print("spendable commit address:", address)
                            scripts.append(script)
            return scripts

    def payout_recover(self, scripts):
        with self.mutex:
            for script in scripts:
                rawtx = self.control.payout_recover(
                    self.payee_wif, script, self.spend_secret
                )
                self.payout_rawtxs.append(rawtx)

    def payout_confirmed(self, minconfirms=1):
        with self.mutex:
            validate.unsigned(minconfirms)
            return self._all_confirmed(self.payout_rawtxs,
                                       minconfirms=minconfirms)
