"""Pacote do próprio plugin guardado no cartão (`insertplay/decky/InsertPlay.zip`).

O cartão leva o plugin: o Decky o instala pelo "Install Plugin from ZIP File" e o
plugin instalado compara versões, se atualiza a partir do cartão ou grava a versão
instalada nele. Sem import de `decky`: tudo aqui roda e é testado fora do Deck.

O zip tem o mesmo layout do scripts/package.sh: uma única pasta `InsertPlay/` na raiz.
O nome importa: o Decky extrai o zip como está em ~/homebrew/plugins, então outra
pasta raiz criaria um segundo plugin em vez de substituir este.
"""
import hashlib
import io
import json
import os
import re
import zipfile

PLUGIN_NAME = "InsertPlay"
CARD_PACKAGE = os.path.join("insertplay", "decky", f"{PLUGIN_NAME}.zip")
# mesmo conteúdo que o scripts/package.sh copia; o resto da pasta instalada fica fora
PACKAGE_ENTRIES = ("plugin.json", "package.json", "main.py", "NOTICE", "py_modules", "dist")
REQUIRED = ("plugin.json", "package.json", "main.py", "dist/index.js")
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.+-]*)?$")
# um pacote real tem algumas centenas de KB; isto barra zip bomb sem limitar o plugin
MAX_UNCOMPRESSED = 64 * 1024 * 1024


class PackageError(Exception):
    pass


def card_package_path(mount: str) -> str:
    return os.path.join(mount, CARD_PACKAGE)


def parse_version(value) -> tuple:
    match = VERSION_RE.match(str(value or "").strip())
    if not match:
        raise PackageError(f"invalid version: {value!r}")
    return tuple(int(part) for part in match.groups())


def compare_versions(a: str, b: str) -> int:
    """-1, 0 ou 1. Só major.minor.patch contam: sufixos de pré-release não ordenam."""
    pa, pb = parse_version(a), parse_version(b)
    return (pa > pb) - (pa < pb)


def _check_plugin_json(data) -> None:
    if not isinstance(data, dict) or data.get("name") != PLUGIN_NAME:
        raise PackageError(f"plugin.json is not the {PLUGIN_NAME} plugin")
    flags = data.get("flags", [])
    # mesma regra do deploy-plugin.sh: o backend nunca roda como root
    if not isinstance(flags, list) or "root" in flags:
        raise PackageError("plugin.json asks for the root flag")


def read_package(zip_path: str) -> dict:
    """Valida o zip do cartão e devolve `{version, sha256, size}`.

    A validação é a mesma que o Decky não faz: o zip vai ser extraído como root em
    ~/homebrew/plugins, então só aceitamos a pasta `InsertPlay/`, sem caminhos
    absolutos, sem `..` e sem symlinks. O arquivo é lido uma vez só, para que o hash
    devolvido (e conferido pelo Decky na instalação) seja dos bytes validados."""
    try:
        with open(zip_path, "rb") as f:
            data = f.read()
    except OSError as exc:
        raise PackageError(f"could not read {zip_path}: {exc}") from exc
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            total = 0
            for info in infos:
                name = info.filename
                parts = name.replace("\\", "/").split("/")
                if name.startswith(("/", "\\")) or ":" in parts[0] or ".." in parts or "\\" in name:
                    raise PackageError(f"unsafe path in zip: {name!r}")
                if parts[0] != PLUGIN_NAME:
                    raise PackageError(f"zip must have a single {PLUGIN_NAME}/ folder, found {name!r}")
                # bits de tipo no external_attr (Unix): 0o120000 = symlink
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise PackageError(f"symlink in zip: {name!r}")
                total += info.file_size
            if total > MAX_UNCOMPRESSED:
                raise PackageError("zip is too large")
            names = {info.filename for info in infos}
            missing = [r for r in REQUIRED if f"{PLUGIN_NAME}/{r}" not in names]
            if missing:
                raise PackageError(f"zip is missing {', '.join(missing)}")
            _check_plugin_json(json.loads(archive.read(f"{PLUGIN_NAME}/plugin.json")))
            package = json.loads(archive.read(f"{PLUGIN_NAME}/package.json"))
    except (zipfile.BadZipFile, ValueError, KeyError) as exc:
        raise PackageError(f"invalid zip {zip_path}: {exc}") from exc
    version = str(package.get("version") or "") if isinstance(package, dict) else ""
    parse_version(version)
    return {"version": version, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def _package_files(plugin_dir: str):
    for entry in PACKAGE_ENTRIES:
        path = os.path.join(plugin_dir, entry)
        if os.path.isfile(path):
            yield path, entry
        elif os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                dirs[:] = sorted(d for d in dirs if d != "__pycache__")
                for name in sorted(files):
                    if name.endswith(".pyc"):
                        continue
                    full = os.path.join(root, name)
                    yield full, os.path.relpath(full, plugin_dir).replace(os.sep, "/")


def build_zip_from_dir(plugin_dir: str, dest_zip: str) -> dict:
    """Empacota a pasta instalada do plugin em `dest_zip` e devolve `read_package` dele.

    Escreve em `<dest>.tmp`, faz fsync e só então renomeia: cartão removido no meio da
    gravação deixa o zip antigo intacto (ou um .tmp órfão), nunca um zip pela metade."""
    os.makedirs(os.path.dirname(dest_zip), exist_ok=True)
    tmp = dest_zip + ".tmp"
    try:
        with open(tmp, "wb") as f:
            with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as archive:
                for full, rel in _package_files(plugin_dir):
                    archive.write(full, f"{PLUGIN_NAME}/{rel}")
            f.flush()
            os.fsync(f.fileno())
        # valida antes de substituir: nunca troca um pacote bom por um quebrado
        read_package(tmp)
        os.replace(tmp, dest_zip)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(os.path.dirname(dest_zip))
    return read_package(dest_zip)


def _fsync_dir(path: str) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return  # Windows não abre diretório; no Deck isto sempre funciona
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def card_status(mount: str, installed_version: str) -> dict:
    """Situação do pacote de um cartão em relação ao plugin instalado.

    `relation`: `missing` (sem zip), `invalid`, `newer` (o cartão traz versão mais
    nova), `older` ou `same`."""
    path = card_package_path(mount)
    out = {"zipPath": path, "version": None, "sha256": None, "relation": "missing", "error": None}
    if not os.path.isfile(path):
        return out
    try:
        out.update(read_package(path))
    except PackageError as exc:
        out.update(relation="invalid", error=str(exc))
        return out
    try:
        cmp = compare_versions(out["version"], installed_version)
    except PackageError:
        cmp = 1  # versão instalada ilegível: qualquer pacote válido do cartão serve
    out["relation"] = {1: "newer", 0: "same", -1: "older"}[cmp]
    return out
