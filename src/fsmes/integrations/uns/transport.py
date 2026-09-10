"""Where a published event actually goes.

Three implementations, one interface:

* `MqttTransport` — a real broker, over `aiomqtt`. The client lives in the
  `[mqtt]` extra, because a plant PC that does not speak MQTT should not
  carry an MQTT library (the same reason `mcp` and `agent` are extras).
* `LogTransport` — builds every topic and payload and writes them to the
  log. This is how a plant sees its namespace before anyone has stood up a
  broker, and how this repository's own tests run: nothing here starts a
  broker on the machine it is developed on.
* whatever a test injects — the interface is three methods.

A publish that fails raises. That is the whole error contract: the caller
records the failure against the message, backs off, and tries again, so a
broker that is down for an hour costs an hour of latency and no events.
"""

from __future__ import annotations

from typing import Protocol
from urllib.parse import unquote, urlsplit

import structlog

from fsmes import shadow
from fsmes.config import Settings

log = structlog.get_logger("uns.transport")

DEFAULT_PORTS = {"mqtt": 1883, "mqtts": 8883, "ws": 80, "wss": 443}


class UnsTransport(Protocol):
    async def connect(self) -> None:
        """Open the connection, or raise. Called before the first publish and
        again after a failure."""
        ...

    async def publish(self, topic: str, payload: bytes, *, qos: int, retain: bool) -> None:
        """Deliver one event, or raise."""
        ...

    async def close(self) -> None:
        """Release the connection. Must be safe to call twice."""
        ...


class BrokerAddress:
    """A broker URL taken apart, so a wrong one fails at start-up and not on
    the first event of the shift."""

    def __init__(self, url: str, username: str = "", password: str = "") -> None:
        parts = urlsplit(url if "://" in url else f"mqtt://{url}")
        scheme = (parts.scheme or "mqtt").lower()
        if scheme not in DEFAULT_PORTS:
            raise ValueError(
                f"unknown broker scheme {scheme!r} in {url!r} "
                f"(known: {', '.join(sorted(DEFAULT_PORTS))})")
        if not parts.hostname:
            raise ValueError(f"broker URL {url!r} names no host")
        self.url = url
        self.scheme = scheme
        self.host = parts.hostname
        self.port = parts.port or DEFAULT_PORTS[scheme]
        self.tls = scheme in ("mqtts", "wss")
        # Credentials in the URL are accepted because that is how brokers are
        # usually written down, but the settings win: a password belongs in an
        # environment variable, not in a URL that ends up in a log line.
        self.username = username or unquote(parts.username or "")
        self.password = password or unquote(parts.password or "")

    def __repr__(self) -> str:  # never the password
        return f"BrokerAddress({self.scheme}://{self.host}:{self.port}, tls={self.tls})"


class LogTransport:
    """Publishes nothing; writes what it would have published.

    Every topic and every payload, at INFO. It is a real transport, not a
    stub: `MES_UNS_MODE=log` is a supported way to run, and the sent count
    it keeps is what a test asserts against.
    """

    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes]] = []

    async def connect(self) -> None:
        log.info("unified namespace in log mode — no broker is contacted")

    async def publish(self, topic: str, payload: bytes, *, qos: int, retain: bool) -> None:
        self.sent.append((topic, payload))
        log.info("would publish", topic=topic, bytes=len(payload), qos=qos, retain=retain)

    async def close(self) -> None:
        return None


class MqttTransport:
    """A real broker over aiomqtt.

    Connects lazily and reconnects after a failure, because the process must
    survive a broker restart without a person. The MES never blocks on this:
    events wait in the outbox while the broker is away.
    """

    def __init__(self, address: BrokerAddress, client_id: str = "fsmes") -> None:
        self.address = address
        self.client_id = client_id
        self._client = None

    async def connect(self) -> None:
        if self._client is not None:
            return
        shadow.guard("uns.mqtt_connect", detail=repr(self.address))
        aiomqtt = _import_aiomqtt()
        kwargs: dict = {
            "hostname": self.address.host,
            "port": self.address.port,
            "identifier": self.client_id,
        }
        if self.address.username:
            kwargs["username"] = self.address.username
            kwargs["password"] = self.address.password
        if self.address.tls:
            import ssl

            kwargs["tls_context"] = ssl.create_default_context()
        client = aiomqtt.Client(**kwargs)
        await client.__aenter__()
        self._client = client
        log.info("connected to broker", broker=repr(self.address), client_id=self.client_id)

    async def publish(self, topic: str, payload: bytes, *, qos: int, retain: bool) -> None:
        shadow.guard("uns.mqtt_publish", detail=topic)
        if self._client is None:
            await self.connect()
        try:
            await self._client.publish(topic, payload=payload, qos=qos, retain=retain)
        except Exception:
            # Drop the connection so the next attempt builds a fresh one; a
            # half-dead client that accepts publishes nobody receives is the
            # failure this is here to prevent.
            await self.close()
            raise

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            await client.__aexit__(None, None, None)
        except Exception:  # closing a broken connection is not news
            log.debug("broker connection did not close cleanly")


NO_CLIENT = (
    "MQTT support is an optional extra. Install it with "
    "`pip install 'factorysemantics-mes[mqtt]'`, or set MES_UNS_MODE=log "
    "to see the topics without a broker."
)


def _import_aiomqtt():
    """The client, or an error that says how to get one."""
    try:
        import aiomqtt
    except ImportError as exc:
        raise RuntimeError(NO_CLIENT) from exc
    if aiomqtt is None:  # the module hidden rather than absent
        raise RuntimeError(NO_CLIENT)
    return aiomqtt


def make_transport(settings: Settings) -> UnsTransport | None:
    """The transport this plant's settings ask for. None means 'off'."""
    mode = (settings.uns_mode or "off").lower()
    if mode == "off":
        return None
    if mode == "log":
        return LogTransport()
    if mode == "mqtt":
        # Settings already refused this pairing at start-up; refused again
        # here so a caller that builds its own Settings cannot get past it.
        shadow.guard("uns.transport", detail=f"broker {settings.uns_broker_url}")
        return MqttTransport(
            BrokerAddress(settings.uns_broker_url, settings.uns_username, settings.uns_password),
            client_id=settings.uns_client_id)
    raise ValueError(f"unknown UNS mode {settings.uns_mode!r} (known: off, log, mqtt)")
