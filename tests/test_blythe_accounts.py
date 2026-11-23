"""Each Windmill run logs in to Blythe as the person who started it (f/dhr/blythe.py).

Their key, 2FA secret and Blythe username come from their own Windmill variables under
u/<their name>/. Here those variables are a dictionary, the key is a fresh one, and Blythe
is a stand-in that records who logged in and with which 2FA code.
"""

import io

import paramiko
import pyotp
import pytest

from f.dhr import blythe
from f.dhr.blythe import Account, BlytheError, load_account

# Throwaway keys made for each test run: the person's key, and a stand-in for Blythe's host key.
KEY = paramiko.ECDSAKey.generate()
HOST_KEY = paramiko.ECDSAKey.generate()


def private_key(passphrase=None):
    out = io.StringIO()
    KEY.write_private_key(out, password=passphrase)
    return out.getvalue()


def variables_for(user, **changes):
    secret = pyotp.random_base32()
    found = {
        f"u/{user}/blythe_username": "csabcd",
        f"u/{user}/blythe_ssh_key": private_key("pass phrase"),
        f"u/{user}/blythe_key_passphrase": "pass phrase",
        f"u/{user}/blythe_totp_secret": secret,
    }
    for name, value in changes.items():
        path = f"u/{user}/{name}"
        if value is None:
            found.pop(path)
        else:
            found[path] = value
    asked = []

    def get_variable(path):
        asked.append(path)
        if path not in found:
            raise RuntimeError(f"404 variable {path} not found")  # as Windmill answers
        return found[path]

    return get_variable, asked, secret


def test_a_teammate_logs_in_as_themselves():
    get_variable, asked, secret = variables_for("ks0610")
    account = load_account("ks0610", get_variable)
    assert account.user == "ks0610" and account.username == "csabcd"
    assert account.totp.now() == pyotp.TOTP(secret).now()
    assert all(path.startswith("u/ks0610/") for path in asked)  # never anyone else's


def test_the_run_s_own_user_is_used_by_default(monkeypatch):
    get_variable, asked, _ = variables_for("markcoding05")
    monkeypatch.setenv("WM_USERNAME", "markcoding05")
    assert load_account(get_variable=get_variable).user == "markcoding05"
    assert asked and all(path.startswith("u/markcoding05/") for path in asked)


def test_rowan_keeps_working_without_the_new_username_variable():
    get_variable, _, _ = variables_for("rshields2004", blythe_username=None)
    assert load_account("rshields2004", get_variable).username == "csujxb"
    get_variable, _, _ = variables_for("rshields2004", blythe_username="csother")
    assert load_account("rshields2004", get_variable).username == "csother"  # the variable wins


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"blythe_username": None}, "u/ks0610/blythe_username is missing"),
        ({"blythe_ssh_key": None}, "u/ks0610/blythe_ssh_key is missing"),
        ({"blythe_totp_secret": ""}, "u/ks0610/blythe_totp_secret is missing"),
        ({"blythe_username": "CS; rm -rf ~"}, "isn't a Blythe username"),
        ({"blythe_totp_secret": "not base32 !!"}, "isn't a valid 2FA secret"),
        ({"blythe_key_passphrase": "wrong"}, "Couldn't open the Blythe SSH key"),
    ],
)
def test_a_person_without_their_own_account_set_up_is_stopped(changes, message):
    get_variable, _, _ = variables_for("ks0610", **changes)
    with pytest.raises(BlytheError, match=message):
        load_account("ks0610", get_variable)


def test_a_key_without_a_passphrase_is_fine():
    get_variable, _, _ = variables_for(
        "ks0610", blythe_ssh_key=private_key(), blythe_key_passphrase=None
    )
    assert load_account("ks0610", get_variable).username == "csabcd"


class FakeTransport:
    """Blythe's login node: asks for the key, then a 2FA code, and records both."""

    logins = []

    def __init__(self, sock):
        self.active = True

    def start_client(self, timeout):
        pass

    def get_remote_server_key(self):
        return HOST_KEY

    def auth_publickey(self, username, key):
        self.username = username
        return ["keyboard-interactive"]

    def is_authenticated(self):
        return False

    def auth_interactive(self, username, handler):
        code = handler("", "", [("Enter 2FA verification code: ", False)])[0]
        FakeTransport.logins.append((self.username, username, code))

    def set_keepalive(self, seconds):
        self.keepalive = seconds

    def is_active(self):
        return self.active

    def close(self):
        self.active = False


def test_the_login_uses_the_person_s_own_username_and_2fa(monkeypatch):
    monkeypatch.setattr(blythe, "KNOWN_HOST_KEYS", {blythe.fingerprint(HOST_KEY)})
    monkeypatch.setattr(blythe.socket, "create_connection", lambda *a, **k: None)
    monkeypatch.setattr(paramiko, "Transport", FakeTransport)
    monkeypatch.setattr(blythe.Blythe, "sh", lambda self, command: (0, "/home/c/csabcd\n", ""))
    FakeTransport.logins = []
    get_variable, _, secret = variables_for("ks0610")
    account = load_account("ks0610", get_variable)

    with blythe.Blythe(account, keepalive=30) as b:
        assert b.home == "/home/c/csabcd" and b.alive()
        assert b.transport.keepalive == 30
    assert not b.alive()
    assert FakeTransport.logins == [("csabcd", "csabcd", pyotp.TOTP(secret).now())]


def test_an_unknown_host_key_stops_the_login_before_anything_is_sent(monkeypatch):
    monkeypatch.setattr(blythe.socket, "create_connection", lambda *a, **k: None)
    monkeypatch.setattr(paramiko, "Transport", FakeTransport)
    FakeTransport.logins = []
    account = Account("ks0610", "csabcd", object(), pyotp.TOTP(pyotp.random_base32()))
    with pytest.raises(BlytheError, match="isn't a known one"):
        blythe.Blythe(account).connect()
    assert FakeTransport.logins == []
