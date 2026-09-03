from __future__ import annotations

from dataclasses import dataclass

_MAX_EVENT_NAME_BYTES = 128
_MAX_DATA_LINES = 4096


class SSEError(ValueError):
    """Raised when a server-sent event stream violates its bounds."""


@dataclass(frozen=True)
class SSEEvent:
    event: str
    data: str


class SSEParser:
    """Incrementally parse bounded UTF-8 server-sent events."""

    def __init__(self, maximum_event_bytes: int) -> None:
        if (
            isinstance(maximum_event_bytes, bool)
            or not isinstance(maximum_event_bytes, int)
            or maximum_event_bytes <= 0
        ):
            raise ValueError("maximum_event_bytes must be a positive integer.")
        self._maximum = maximum_event_bytes
        self._buffer = bytearray()
        self._event = b""
        self._data: list[bytes] = []
        self._data_bytes = 0
        self._event_wire_bytes = 0
        self._closed = False

    def feed(self, chunk: bytes) -> tuple[SSEEvent, ...]:
        if self._closed:
            raise SSEError("The server-sent event parser is closed.")
        if not isinstance(chunk, bytes):
            raise TypeError("SSE chunks must be bytes.")
        if b"\0" in chunk:
            raise SSEError("Kernel stream contains a NUL byte.")
        self._buffer.extend(chunk)
        events: list[SSEEvent] = []
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                if self._event_wire_bytes + len(self._buffer) > self._maximum:
                    raise SSEError("Kernel event exceeded the transport limit.")
                return tuple(events)
            line = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            wire_bytes = len(line) + 1
            if line.endswith(b"\r"):
                line = line[:-1]
            event = self._line(line, wire_bytes=wire_bytes)
            if event is not None:
                events.append(event)

    def close(self) -> tuple[SSEEvent, ...]:
        """Finish the stream and dispatch its final unterminated event."""

        if self._closed:
            return ()
        self._closed = True
        events: list[SSEEvent] = []
        if self._buffer:
            line = bytes(self._buffer)
            self._buffer.clear()
            wire_bytes = len(line)
            if line.endswith(b"\r"):
                line = line[:-1]
            event = self._line(line, wire_bytes=wire_bytes)
            if event is not None:
                events.append(event)
        event = self._line(b"", wire_bytes=0)
        if event is not None:
            events.append(event)
        return tuple(events)

    def _line(self, line: bytes, *, wire_bytes: int) -> SSEEvent | None:
        if not line:
            if not self._data:
                self._reset()
                return None
            event_bytes = self._event or b"message"
            try:
                event = event_bytes.decode("utf-8", errors="strict")
                data = b"\n".join(self._data).decode(
                    "utf-8",
                    errors="strict",
                )
            except UnicodeDecodeError as error:
                raise SSEError("Kernel stream contains invalid UTF-8.") from error
            self._reset()
            return SSEEvent(event=event, data=data)

        self._event_wire_bytes += wire_bytes
        if self._event_wire_bytes > self._maximum:
            raise SSEError("Kernel event exceeded the transport limit.")

        if line.startswith(b":"):
            return None
        field, separator, value = line.partition(b":")
        if separator and value.startswith(b" "):
            value = value[1:]
        if field == b"event":
            if not value or len(value) > _MAX_EVENT_NAME_BYTES:
                raise SSEError("Kernel event name is invalid.")
            self._event = value
        elif field == b"data":
            if len(self._data) == _MAX_DATA_LINES:
                raise SSEError("Kernel event has too many data lines.")
            next_bytes = self._data_bytes + len(value)
            if self._data:
                next_bytes += 1
            if next_bytes > self._maximum:
                raise SSEError("Kernel event exceeded the transport limit.")
            self._data.append(value)
            self._data_bytes = next_bytes
        return None

    def _reset(self) -> None:
        self._event = b""
        self._data = []
        self._data_bytes = 0
        self._event_wire_bytes = 0
