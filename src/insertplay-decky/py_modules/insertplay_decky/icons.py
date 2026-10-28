"""Ícone dos atalhos (issue #17).

Medido no Deck (2026-10-27): o Steam guarda só o *caminho* do ícone de um atalho e o
carrega sob demanda. Com o arquivo no cartão, o atalho fica sem ícone quando o Steam
reinicia com o cartão fora, e uma falha num caminho só passa no próximo reinício. Por
isso o ícone é copiado para o SSD, com o hash do conteúdo no nome: um ícone novo no
cartão vira um caminho novo, sem esbarrar no que o Steam guardou de um caminho antigo.
"""
import glob
import hashlib
import os
import shutil
import struct
import time

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


def content_hash(source: str) -> str:
    sha = hashlib.sha256()
    with open(source, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()[:HASH_CHARS]


def target_path(current, source: str, guid: str, icons_dir: str, now=time.time) -> str:
    """Caminho da cópia de `source` no SSD: `<guid>-<hash>-<geração>.<ext>`.

    Mesmo conteúdo já aplicado → o caminho atual, e não há o que fazer. Conteúdo novo →
    sempre um caminho inédito, mesmo que o conteúdo já tenha sido usado antes: o Steam
    não relê um caminho que já carregou na sessão (A → B → A ficaria em B até reiniciar)."""
    digest = content_hash(source)
    if current and _ours(current, icons_dir):
        stem = os.path.splitext(os.path.basename(current))[0]
        if stem == f"{guid}-{digest}" or stem.startswith(f"{guid}-{digest}-"):
            return current
    ext = os.path.splitext(source)[1].lower() or ".png"
    generation = int(now() * 1000)
    while True:
        path = os.path.join(icons_dir, f"{guid}-{digest}-{generation:x}{ext}")
        if not os.path.exists(path):
            return path
        generation += 1


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


def referenced_icons(steam_root: str) -> set:
    """Ícones que algum atalho de alguma conta Steam usa. A pasta de cópias é do
    aparelho, não da conta: uma versão antiga pode estar em uso na outra conta.
    Lança se um `shortcuts.vdf` não puder ser lido: na dúvida, nada é apagado."""
    used = set()
    for path in glob.glob(os.path.join(steam_root, "userdata", "*", "config", "shortcuts.vdf")):
        used.update(os.path.normpath(icon) for icon in shortcut_icons(path).values() if icon)
    return used


def prune(icons_dir: str, keep: set) -> list:
    """Apaga as cópias fora de `keep` (caminhos em uso). A versão substituída só sai numa
    chamada seguinte, depois que o Steam gravou a nova no `shortcuts.vdf`: se o
    `SetShortcutIcon` falhar, o atalho não fica apontando para um arquivo apagado."""
    removed = []
    try:
        names = os.listdir(icons_dir)
    except FileNotFoundError:
        return removed
    keep = {os.path.normpath(path) for path in keep}
    for name in names:
        path = os.path.normpath(os.path.join(icons_dir, name))
        if path in keep:
            continue
        try:
            os.remove(path)
            removed.append(name)
        except OSError:
            pass
    return removed
