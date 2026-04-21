"""
SSE log streaming — trimite log-urile in timp real catre browser.
"""
import asyncio
import logging
from collections import deque
from typing import List

# Buffer ultimele 500 linii (pentru clientii care se conecteaza tarziu)
recent_logs: deque = deque(maxlen=500)

# Cate o coada per client SSE conectat
_clients: List[asyncio.Queue] = []


class SSELogHandler(logging.Handler):
    """Handler care trimite fiecare linie de log catre toti clientii SSE."""

    def emit(self, record: logging.LogRecord) -> None:
        line = self.format(record)
        recent_logs.append(line)
        for q in list(_clients):
            try:
                q.put_nowait(line)
            except asyncio.QueueFull:
                pass


def install_handler() -> None:
    """Instaleaza handler-ul SSE pe root logger."""
    handler = SSELogHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.addHandler(handler)
    # Forteaza flush imediat pe stdout ca log-urile sa apara sincron in terminal
    for h in root.handlers:
        if hasattr(h, "stream"):
            h.stream.reconfigure(line_buffering=True) if hasattr(h.stream, "reconfigure") else None


async def log_event_generator(request):
    """Generator async pentru SSE — trimite log-urile unui client."""
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _clients.append(q)

    try:
        # Trimite bufferul recent la conectare
        for line in list(recent_logs):
            yield f"data: {line}\n\n"

        while True:
            if await request.is_disconnected():
                break
            try:
                line = await asyncio.wait_for(q.get(), timeout=15.0)
                # Scapa newline-urile din linie ca sa nu strice protocolul SSE
                line = line.replace("\n", " ").replace("\r", "")
                yield f"data: {line}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
    finally:
        if q in _clients:
            _clients.remove(q)
