import time
from collections.abc import Callable
from functools import partial
from pathlib import Path

import requests

REQUEST_TIMEOUT_S = 120


def atomic_write(path: Path, data: bytes) -> None:
    from wheatwatch.regions import _atomic_write

    def write(tmp: Path) -> None:
        tmp.write_bytes(data)

    _atomic_write(path, write)


def _http_get(url: str) -> bytes:
    from wheatwatch.climate import HttpStatusError

    response = requests.get(url, timeout=REQUEST_TIMEOUT_S)
    if response.status_code != 200:
        raise HttpStatusError(response.status_code)
    return response.content


def get_with_retry(
    url: str,
    *,
    fetch: Callable[[str], bytes] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    from wheatwatch.ndvi import _retry

    return _retry(partial(fetch or _http_get, url), sleep)
