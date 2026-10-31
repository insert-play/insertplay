"""Persistência local do plugin: registro guid → atalho, configurações e log de eventos."""
import json
import os
import tempfile
import time


def _load(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path: str, data) -> None:
    """Escrita atômica: um plugin_loader reiniciado no meio da escrita não pode corromper o registro."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


class Registry:
    """`guid → {appid, appName, artworkApplied}`. The appid comes from Steam
    (AddShortcut) and can't be computed in advance, so it has to be stored."""

    def __init__(self, path: str):
        self.path = path

    def all(self) -> dict:
        data = _load(self.path, {})
        return data if isinstance(data, dict) else {}

    def get(self, guid: str):
        return self.all().get(guid)

    def put(self, guid: str, record: dict) -> dict:
        data = self.all()
        merged = {**data.get(guid, {}), **record, "updatedAt": time.time()}
        data[guid] = merged
        _save(self.path, data)
        return merged

    def forget(self, guid: str) -> None:
        data = self.all()
        if data.pop(guid, None) is not None:
            _save(self.path, data)


DEFAULT_SETTINGS = {"autoLaunch": True, "hideRemoved": True}


class Settings:
    def __init__(self, path: str):
        self.path = path

    def all(self) -> dict:
        data = _load(self.path, {})
        return {**DEFAULT_SETTINGS, **(data if isinstance(data, dict) else {})}

    def set(self, key: str, value) -> dict:
        if key not in DEFAULT_SETTINGS or type(value) is not type(DEFAULT_SETTINGS[key]):
            raise ValueError(f"unknown setting or wrong type: {key}={value!r}")
        data = self.all()
        data[key] = value
        _save(self.path, data)
        return data


class EventLog:
    """JSONL só com acréscimo; corta para as últimas `keep` linhas ao passar de `limit`."""

    def __init__(self, path: str, limit: int = 1000, keep: int = 500):
        self.path, self.limit, self.keep = path, limit, keep

    def add(self, kind: str, message: str) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        line = json.dumps({"ts": time.time(), "kind": kind, "message": message}, ensure_ascii=False)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        lines = self._lines()
        if len(lines) > self.limit:
            with open(self.path, "w", encoding="utf-8") as f:
                f.writelines(l + "\n" for l in lines[-self.keep:])

    def _lines(self) -> list:
        try:
            with open(self.path, encoding="utf-8") as f:
                return [l.rstrip("\n") for l in f if l.strip()]
        except OSError:
            return []

    def tail(self, count: int = 50) -> list:
        out = []
        for line in self._lines()[-count:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return list(reversed(out))
