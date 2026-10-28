"""Tests for what an account is called where its username must not appear (#241)."""

from bartleby.runtime.clients.credentials import account_label
from bartleby.shared.account_store import AccountStore


def test_an_account_with_an_alias_is_called_by_it():
    AccountStore().set_accounts([{"username": "jdoe1234", "alias": "Main"}])
    assert account_label("jdoe1234") == "Main"


def test_an_account_without_one_is_censored():
    AccountStore().set_accounts([{"username": "jdoe1234", "alias": ""}])
    assert account_label("jdoe1234") == "j***4"


def test_an_account_the_store_does_not_know_is_censored_too():
    AccountStore().set_accounts([])
    assert account_label("jdoe1234") == "j***4"
