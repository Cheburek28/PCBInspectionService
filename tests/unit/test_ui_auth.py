from __future__ import annotations

from pcb_inspection.api.ui import auth


def test_password_check() -> None:
    assert auth.check_password("s3cret", "s3cret")
    assert not auth.check_password("s3cret", "s3cret ")
    assert not auth.check_password("s3cret", "")


def test_token_round_trip() -> None:
    token = auth.make_token("pw", hours=1, now=1000)
    assert auth.token_valid("pw", token, now=1000 + 3599)


def test_token_expires() -> None:
    token = auth.make_token("pw", hours=1, now=1000)
    assert not auth.token_valid("pw", token, now=1000 + 3601)


def test_token_is_bound_to_the_password() -> None:
    assert not auth.token_valid("other", auth.make_token("pw", hours=1))


def test_tampered_tokens() -> None:
    token = auth.make_token("pw", hours=1)
    expires, sig = token.split(".")
    assert not auth.token_valid("pw", f"{int(expires) + 999}.{sig}")
    assert not auth.token_valid("pw", f"{expires}.{'0' * len(sig)}")
    for bad in (None, "", "nodot", "abc.def"):
        assert not auth.token_valid("pw", bad)
