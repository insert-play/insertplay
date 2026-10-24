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
import tempfile
import zipfile

PLUGIN_NAME = "InsertPlay"
CARD_PACKAGE = os.path.join("insertplay", "decky", f"{PLUGIN_NAME}.zip")
# o que entra no pacote, seja da pasta do projeto (package.sh) ou da instalada (plugin);
# o `upload` do scripts/deck.mjs envia a mesma lista
PACKAGE_ENTRIES = ("plugin.json", "package.json", "main.py", "NOTICE", "py_modules", "dist")
REQUIRED = ("plugin.json", "package.json", "main.py", "dist/index.js")
# semver: major.minor.patch[-pré-release][+build]
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")
# um pacote real tem algumas dezenas de KB; isto barra zip bomb e arquivo gigante com
# o nome certo sem limitar o plugin
MAX_ZIP_BYTES = 16 * 1024 * 1024
MAX_UNCOMPRESSED = 64 * 1024 * 1024

# zip_path -> ((mtime_ns, size), resultado ou PackageError). O painel pergunta o status
# a cada 5 s; sem isto cada pergunta relia e refazia o hash do zip no cartão.
_cache: dict = {}


class PackageError(Exception):
    pass


def card_package_path(mount: str) -> str:
    return os.path.join(mount, CARD_PACKAGE)


def parse_version(value) -> tuple:
    """Chave de ordenação com a precedência do semver: `1.0.0-beta < 1.0.0`, e
    identificadores de pré-release numéricos ordenam como número. `+build` não conta."""
    match = VERSION_RE.match(str(value or "").strip())
    if not match:
        raise PackageError(f"invalid version: {value!r}")
    core = tuple(int(part) for part in match.group(1, 2, 3))
    pre = match.group(4)
    if not pre:
        return core + ((1,),)
    ids = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))
    return core + ((0,) + ids,)


def compare_versions(a: str, b: str) -> int:
    """-1, 0 ou 1."""
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
    absolutos, sem `..`, sem symlinks e sem dois nomes para o mesmo arquivo
    (`InsertPlay/./plugin.json` é extraído por cima do `plugin.json` validado). Todo
    arquivo é descompactado e tem o CRC conferido: o Decky apaga a versão instalada
    antes de extrair, e um zip que falha no meio deixaria o usuário sem plugin.

    O arquivo é lido uma vez só, para que o hash devolvido (e conferido pelo Decky na
    instalação) seja dos bytes validados."""
    try:
        if os.path.getsize(zip_path) > MAX_ZIP_BYTES:
            raise PackageError(f"{zip_path} is too large")
        with open(zip_path, "rb") as f:
            data = f.read()
    except OSError as exc:
        raise PackageError(f"could not read {zip_path}: {exc}") from exc
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            total = 0
            seen = set()
            for info in infos:
                name = info.filename
                parts = name.split("/")
                # pasta termina em "/": só o último componente pode ser vazio
                inner = parts[:-1] if info.is_dir() else parts
                if (
                    "\\" in name
                    or ":" in name
                    or any(part in ("", ".", "..") for part in inner)
                ):
                    raise PackageError(f"unsafe path in zip: {name!r}")
                if name in seen:
                    raise PackageError(f"duplicate entry in zip: {name!r}")
                seen.add(name)
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
            # descompacta tudo e confere o CRC; erro de descompressão também cai abaixo
            bad = archive.testzip()
            if bad is not None:
                raise PackageError(f"corrupt file in zip: {bad!r}")
            _check_plugin_json(json.loads(archive.read(f"{PLUGIN_NAME}/plugin.json")))
            package = json.loads(archive.read(f"{PLUGIN_NAME}/package.json"))
    except PackageError:
        raise
    except Exception as exc:
        # zip corrompido falha de muitos jeitos (BadZipFile, zlib.error, EOFError,
        # NotImplementedError, RuntimeError de entrada cifrada...); todos são "inválido"
        raise PackageError(f"invalid zip {zip_path}: {type(exc).__name__}: {exc}") from exc
    version = str(package.get("version") or "") if isinstance(package, dict) else ""
    parse_version(version)
    return {"version": version, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def read_package_cached(zip_path: str) -> dict:
    """`read_package`, refeito só quando o arquivo muda (mtime ou tamanho)."""
    st = os.stat(zip_path)
    key = (st.st_mtime_ns, st.st_size)
    hit = _cache.get(zip_path)
    if not hit or hit[0] != key:
        try:
            hit = (key, read_package(zip_path))
        except PackageError as exc:
            hit = (key, exc)
        _cache[zip_path] = hit
    if isinstance(hit[1], PackageError):
        raise hit[1]
    return dict(hit[1])


def _package_files(plugin_dir: str):
    for entry in PACKAGE_ENTRIES:
        path = os.path.join(plugin_dir, entry)
        if os.path.isfile(path):
            yield path, entry
        elif os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                dirs[:] = sorted(d for d in dirs if d != "__pycache__")
                for name in sorted(files):
                    if name.endswith((".pyc", ".map")):
                        continue
                    full = os.path.join(root, name)
                    yield full, os.path.relpath(full, plugin_dir).replace(os.sep, "/")


def _card_package_dir(mount: str) -> str:
    """`<cartão>/insertplay/decky`, criada se faltar, garantidamente dentro do cartão.

    O cartão é conteúdo de terceiros: um `insertplay` ou `decky` que seja symlink
    levaria a gravação para fora dele."""
    path = os.path.realpath(mount)
    for part in os.path.dirname(CARD_PACKAGE).split(os.sep):
        path = os.path.join(path, part)
        if os.path.islink(path):
            raise PackageError(f"{path} is a symlink")
        if not os.path.exists(path):
            os.mkdir(path)
        elif not os.path.isdir(path):
            raise PackageError(f"{path} is not a directory")
    return path


def _write_atomic(dest_zip: str, fill) -> dict:
    """Grava o zip com `fill(arquivo)`, valida e só então troca `dest_zip`.

    O temporário é criado com O_EXCL e nome aleatório (mkstemp), então nunca segue um
    symlink que já esteja no cartão. fsync antes do rename: cartão removido no meio da
    gravação deixa o zip antigo intacto (ou um temporário órfão), nunca um zip pela
    metade. `os.replace` troca o próprio `dest_zip`, mesmo se ele for um symlink."""
    folder = os.path.dirname(dest_zip)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{PLUGIN_NAME}.", suffix=".zip.tmp")
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o644)  # mkstemp cria 0600; o pacote não é segredo
        with os.fdopen(fd, "wb") as f:
            fill(f)
            f.flush()
            os.fsync(f.fileno())
        # valida antes de substituir: nunca troca um pacote bom por um quebrado
        info = read_package(tmp)
        os.replace(tmp, dest_zip)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(folder)
    # os.replace mantém os bytes validados: guarda o resultado em vez de reler o cartão
    st = os.stat(dest_zip)
    _cache[dest_zip] = ((st.st_mtime_ns, st.st_size), info)
    return dict(info)


def _zip_dir(plugin_dir: str):
    def fill(f):
        with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as archive:
            for full, rel in _package_files(plugin_dir):
                archive.write(full, f"{PLUGIN_NAME}/{rel}")

    return fill


def build_zip_from_dir(plugin_dir: str, dest_zip: str) -> dict:
    """Empacota a pasta do plugin em `dest_zip` (uma pasta do desenvolvedor, como o
    `out/` do package.sh) e devolve `read_package` dele. Para o cartão, use
    `write_card_package`."""
    os.makedirs(os.path.dirname(dest_zip) or ".", exist_ok=True)
    return _write_atomic(dest_zip, _zip_dir(plugin_dir))


def write_card_package(plugin_dir: str, mount: str) -> dict:
    """Empacota a pasta do plugin direto no pacote do cartão."""
    dest = os.path.join(_card_package_dir(mount), os.path.basename(CARD_PACKAGE))
    return _write_atomic(dest, _zip_dir(plugin_dir))


def copy_package_to_card(src_zip: str, mount: str) -> tuple:
    """Copia um zip pronto (out/InsertPlay.zip) para o cartão, sem rebaixar versão:
    se o cartão já tem um pacote válido mais novo, ele fica. Devolve
    `(info do pacote no cartão, copiou?)`."""
    new = read_package(src_zip)
    dest = os.path.join(_card_package_dir(mount), os.path.basename(CARD_PACKAGE))
    if os.path.isfile(dest):
        try:
            old = read_package(dest)
        except PackageError:
            old = None
        if old and compare_versions(old["version"], new["version"]) > 0:
            return old, False

    def fill(f):
        with open(src_zip, "rb") as src:
            while chunk := src.read(1 << 20):
                f.write(chunk)

    return _write_atomic(dest, fill), True


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
        out.update(read_package_cached(path))
    except (PackageError, OSError) as exc:
        out.update(relation="invalid", error=str(exc))
        return out
    try:
        cmp = compare_versions(out["version"], installed_version)
    except PackageError:
        cmp = 1  # versão instalada ilegível: qualquer pacote válido do cartão serve
    out["relation"] = {1: "newer", 0: "same", -1: "older"}[cmp]
    return out
