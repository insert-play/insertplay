"""HTTP client of the save server (docs/remote-library.md#server-api).

One request per call over `http.client`: never follows redirects, only talks to the base
URL's host, and never logs the token. `Offline` covers everything that means "try later":
no route, timeout, refused connection, `5xx` (Caddy answers 502 with the save server down).
"""
import http.client
import json
import os
import socket
import urllib.parse

CHUNK = 1024 * 1024
META_TIMEOUT = 10
TRANSFER_TIMEOUT = 30


class Offline(Exception):
    pass


class ServerError(Exception):
    """The server answered, but not what the contract allows (401, 400, 413…)."""

    def __init__(self, status: int, detail: str = ""):
        super().__init__(f"HTTP {status}{': ' + detail if detail else ''}")
        self.status = status


class Conflict(Exception):
    def __init__(self, meta):
        super().__init__("rev conflict")
        self.meta = meta


class Client:
    def __init__(self, base: str, token: str, device: str, timeout: float = META_TIMEOUT):
        parts = urllib.parse.urlsplit(base)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ServerError(0, "bad server URL")
        self.scheme = parts.scheme
        self.host = parts.hostname
        self.port = parts.port
        self.prefix = (parts.path or "/").rstrip("/") + "/"
        self.token = token
        self.device = device
        self.timeout = timeout

    def _conn(self, timeout: float):
        cls = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        return cls(self.host, self.port, timeout=timeout)

    def _path(self, key: str, rest: str) -> str:
        return f"{self.prefix}saves/{key}/{rest}"

    def _request(self, method: str, key: str, rest: str, body=None, headers=None, length=None, timeout=None):
        h = {"Authorization": f"Bearer {self.token}"}
        h.update(headers or {})
        if body is not None:
            h["Content-Length"] = str(length if length is not None else len(body))
        conn = self._conn(timeout or self.timeout)
        try:
            conn.putrequest(method, self._path(key, rest), skip_accept_encoding=True)
            for name, value in h.items():
                conn.putheader(name, value)
            conn.endheaders()
            if body is not None:
                if isinstance(body, (bytes, bytearray)):
                    conn.send(body)
                else:
                    for chunk in iter(lambda: body.read(CHUNK), b""):
                        conn.send(chunk)
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            raise Offline(f"{type(exc).__name__}: {exc}") from exc
        if resp.status >= 500:
            resp.read()
            conn.close()
            raise Offline(f"HTTP {resp.status}")
        if 300 <= resp.status < 400:
            conn.close()
            raise ServerError(resp.status, "redirects are not followed")
        return conn, resp

    def _json(self, method, key, rest, ok=(200,), **kw):
        conn, resp = self._request(method, key, rest, **kw)
        try:
            raw = resp.read()
        except (OSError, http.client.HTTPException) as exc:
            raise Offline(str(exc)) from exc
        finally:
            conn.close()
        try:
            data = json.loads(raw) if raw else None
        except ValueError:
            data = None
        return resp.status, data

    def meta(self, key: str):
        """The server's meta, or None with no save yet."""
        status, data = self._json("GET", key, "meta")
        if status == 404:
            return None
        if status != 200 or not isinstance(data, dict):
            raise ServerError(status, "meta")
        return data

    def strategy(self, key: str):
        status, data = self._json("GET", key, "strategy.json")
        if status == 404:
            return None
        if status != 200 or not isinstance(data, dict):
            raise ServerError(status, "strategy")
        return data

    def put_strategy(self, key: str, data: dict) -> None:
        raw = json.dumps(data, sort_keys=True).encode("utf-8")
        status, _ = self._json("PUT", key, "strategy.json", body=raw, headers={"Content-Type": "application/json"})
        if status != 200:
            raise ServerError(status, "strategy upload")

    def download(self, key: str, dest: str) -> dict:
        """The save to `dest`. Returns {"rev", "hash"} of that very file (X-Save-*)."""
        conn, resp = self._request("GET", key, "current.tar.gz", timeout=TRANSFER_TIMEOUT)
        tmp = dest + ".partial"
        try:
            if resp.status != 200:
                resp.read()
                raise ServerError(resp.status, "download")
            length = resp.getheader("Content-Length")
            got = 0
            with open(tmp, "wb") as f:
                for chunk in iter(lambda: resp.read(CHUNK), b""):
                    f.write(chunk)
                    got += len(chunk)
            if length is not None and int(length) != got:
                raise Offline("download cut short")
            os.replace(tmp, dest)
            return {"rev": resp.getheader("X-Save-Rev") or "", "hash": (resp.getheader("X-Save-Hash") or "").lower()}
        except (OSError, http.client.HTTPException, socket.timeout) as exc:
            raise Offline(f"download: {exc}") from exc
        finally:
            conn.close()
            if os.path.exists(tmp):
                os.remove(tmp)

    def upload(self, key: str, path: str, rev, snap_hash: str, newest: float, backup: bool) -> dict:
        """PUT current.tar.gz. `rev` None = first save. Raises Conflict on 409."""
        headers = {
            "Content-Type": "application/gzip",
            "X-Save-Hash": snap_hash,
            "X-Save-Newest-Mtime": repr(float(newest)),
            "X-Save-Device": self.device,
            "X-Save-Backup": "1" if backup else "0",
        }
        if rev is None:
            headers["If-None-Match"] = "*"
        else:
            headers["If-Match"] = str(rev)
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            status, data = self._json(
                "PUT", key, "current.tar.gz", body=f, length=size, headers=headers, timeout=TRANSFER_TIMEOUT
            )
        if status == 409:
            raise Conflict((data or {}).get("meta"))
        if status not in (200, 201) or not isinstance(data, dict):
            raise ServerError(status, (data or {}).get("error", "upload") if isinstance(data, dict) else "upload")
        return data

    def backup_limit(self, key: str):
        """The server's backup limit in bytes (None = unknown)."""
        status, data = self._json("GET", key, "backups")
        if status == 200 and isinstance(data, dict) and isinstance(data.get("limit"), int):
            return data["limit"]
        return None

    def backup(self, key: str, path: str) -> dict:
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            status, data = self._json(
                "POST",
                key,
                "backups",
                body=f,
                length=size,
                headers={"Content-Type": "application/gzip", "X-Save-Device": self.device},
                timeout=TRANSFER_TIMEOUT,
            )
        if status != 200 or not isinstance(data, dict):
            raise ServerError(status, "backup")
        return data
