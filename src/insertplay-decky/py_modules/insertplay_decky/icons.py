"""Ícone dos atalhos (issue #17).

Medido no Deck (2026-10-27): o Steam guarda só o *caminho* do ícone de um atalho e o
carrega sob demanda. Com o arquivo no cartão, o atalho fica sem ícone quando o Steam
reinicia com o cartão fora, e uma falha num caminho só passa no próximo reinício. Por
isso o ícone é copiado para o SSD, com o hash do conteúdo no nome: um ícone novo no
cartão vira um caminho novo, sem esbarrar na falha guardada de um caminho antigo.
"""
import hashlib
import os
import shutil
import struct

# steamid64 = este valor + id da conta (a pasta em userdata/)
STEAMID64_BASE = 76561197960265728
HASH_CHARS = 12


def account_id(steamid: str) -> int:
    account = int(steamid) - STEAMID64_BASE
    if account <= 0:
        raise ValueError(f"not a steamid64: {steamid!r}")
    return account


def shortcuts_vdf(steam_root: str, steamid: str) -> str:
    return os.path.join(steam_root, "userdata", str(account_id(steamid)), "config", "shortcuts.vdf")


def _read_map(data: bytes, i: int) -> tuple:
    out = {}
    while True:
        kind = data[i]
        i += 1
        if kind == 0x08:
            return out, i
        end = data.index(b"\x00", i)
        key = data[i:end].decode("utf-8", "replace").lower()
        i = end + 1
        if kind == 0x00:
            out[key], i = _read_map(data, i)
        elif kind == 0x01:
            end = data.index(b"\x00", i)
            out[key] = data[i:end].decode("utf-8", "replace")
            i = end + 1
        elif kind == 0x02:
            out[key] = struct.unpack_from("<i", data, i)[0]
            i += 4
        elif kind == 0x07:
            out[key] = struct.unpack_from("<Q", data, i)[0]
            i += 8
        else:
            raise ValueError(f"unknown VDF type {kind:#x} at byte {i - 1}")


def shortcut_icons(path: str) -> dict:
    """appid → caminho do ícone ("" = sem ícone), lido do `shortcuts.vdf` binário.

    O frontend não enxerga esse caminho (nem `appStore` nem `appDetailsStore`); o
    arquivo é a única fonte. Medido: o Steam o grava ~0,16 s depois de `SetShortcutIcon`.
    O SteamGridDB, por sua vez, edita o arquivo direto."""
    with open(path, "rb") as f:
        data = f.read()
    root, _ = _read_map(data, 0)
    out = {}
    for entry in (root.get("shortcuts") or {}).values():
        if isinstance(entry, dict) and "appid" in entry:
            out[(entry["appid"] & 0xFFFFFFFF) | 0x80000000] = str(entry.get("icon") or "")
    return out


def _ours(path: str, icons_dir: str) -> bool:
    return os.path.dirname(os.path.normpath(path)) == os.path.normpath(icons_dir)


def target_path(source: str, guid: str, icons_dir: str) -> str:
    sha = hashlib.sha256()
    with open(source, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
    ext = os.path.splitext(source)[1].lower() or ".png"
    return os.path.join(icons_dir, f"{guid}-{sha.hexdigest()[:HASH_CHARS]}{ext}")


def plan(current, target: str, icons_dir: str) -> bool:
    """Aplicar `target`? `current` é o ícone que o atalho tem; `None` = o atalho ainda
    não está no `shortcuts.vdf` (acabou de ser criado). Só substitui a falta de ícone ou
    um ícone que o próprio plugin gravou: o resto foi escolha do usuário."""
    if current is None or current == "":
        return True
    return current != target and _ours(current, icons_dir)


def stage(source: str, target: str) -> None:
    """Copia o ícone para `target`, se ainda não estiver lá. Grava num temporário e troca
    de uma vez: o Steam nunca vê um arquivo pela metade."""
    if os.path.isfile(target):
        return
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = f"{target}.tmp"
    shutil.copyfile(source, tmp)
    os.replace(tmp, target)


def prune(icons_dir: str, keep: dict) -> list:
    """Apaga os ícones que nenhum atalho usa mais: versões antigas de um guid (`keep`
    guarda o caminho atual de cada um) e guids fora do registro. Devolve os nomes."""
    removed = []
    try:
        names = os.listdir(icons_dir)
    except FileNotFoundError:
        return removed
    for name in names:
        guid = name[:36]
        path = os.path.join(icons_dir, name)
        if guid in keep and (keep[guid] is None or os.path.normpath(keep[guid]) == os.path.normpath(path)):
            continue
        try:
            os.remove(path)
            removed.append(name)
        except OSError:
            pass
    return removed
