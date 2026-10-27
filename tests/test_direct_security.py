"""The 'type your database details' form can't be turned into an attack tool."""

import pytest

from app.agents import direct


@pytest.fixture(autouse=True)
def fresh_limits():
    direct._attempts.clear()
    yield
    direct._attempts.clear()


def test_new_cookies_dont_give_new_tries():
    for i in range(direct.IP_ATTEMPTS_PER_10_MIN):
        direct._rate_limit(f"visitor-{i}", "203.0.113.9", f"db{i}.example.com")
    with pytest.raises(direct.DirectError, match="Too many tries"):
        direct._rate_limit("visitor-new", "203.0.113.9", "other.example.com")


def test_one_database_cant_be_password_guessed_through_us():
    for i in range(direct.TARGET_ATTEMPTS_PER_10_MIN):
        direct._rate_limit(f"v{i}", f"198.51.100.{i}", "victim.example.com")
    with pytest.raises(direct.DirectError, match="Too many tries"):
        direct._rate_limit("v-other", "198.51.100.200", "VICTIM.example.com")


def test_form_cant_be_used_to_scan_the_internet():
    for i in range(direct.ALL_ATTEMPTS_PER_10_MIN):
        direct._rate_limit(f"v{i}", f"192.0.2.{i % 250}-{i}", f"h{i}.example.com")
    with pytest.raises(direct.DirectError, match="Too many tries"):
        direct._rate_limit("v-x", "192.0.2.251", "new.example.com")


@pytest.mark.parametrize("port", ["22", "25", "80", "443", "21"])
def test_service_ports_are_refused(port):
    with pytest.raises(direct.DirectError, match="above 1024"):
        direct._clean_form({"type": "mysql", "host": "db.example.com", "port": port, "database": "d", "user": "u"})


def test_private_and_own_addresses_are_refused():
    for host in ("127.0.0.1", "10.0.0.5", "192.168.1.10", "169.254.169.254", "localhost"):
        with pytest.raises(direct.DirectError):
            direct.public_ip(host, 3306)


def test_fake_server_cant_ask_for_our_files():
    import inspect

    from connector import goal_connector

    src = inspect.getsource(goal_connector)
    assert "local_infile=False" in src and "passfile=os.devnull" in src


def test_unknown_errors_dont_echo_the_other_server():
    import inspect

    assert "Couldn't connect: {msg" not in inspect.getsource(direct)
