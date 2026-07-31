import socket

import pytest


class NetworkAccessDenied(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """The suite is documented as running offline. Enforce it rather than
    trusting every test to substitute the network for itself."""

    def deny(*args, **kwargs):
        raise NetworkAccessDenied(
            "a test tried to open a real connection. Substitute the network "
            "instead: bundle.load takes a verifier_factory and refresh takes "
            "a download."
        )

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
