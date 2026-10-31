"""Save portátil (issue #13): dados do Proton de um jogo Steam do cartão moram no
cartão, ligados ao SSD por symlink enquanto o cartão está inserido. Dois modos:

- `userdata`: só a pasta do usuário Windows (`pfx/drive_c/users/steamuser`: Documents,
  AppData, Saved Games). O resto do prefixo fica no SSD de cada aparelho. Medido: o
  Proton recopia ~115 MB de DLLs para o prefixo a cada abertura; num cartão lento isso
  levava minutos, com o prefixo no SSD leva menos de 1 s;
- `prefix`: o prefixo inteiro (`compatdata/<appid>`). Leva também o registro, mas paga
  aquela cópia no cartão a cada abertura.

Sem import de `decky`: tudo aqui roda e é testado fora do Deck. Medições em
docs/steam-api-notes.md ("Prefixo do Proton no cartão via symlink").

Regras:
- nada é apagado. O que perde um conflito é renomeado para backup;
- os dados de um jogo aberto não são tocados;
- só mexemos em symlink que nós criamos (destino em `.insertplay/prefixes/` ou
  `.insertplay/users/`)."""
import os
import re
import shutil
import stat
import time

from .store import _load, _save

CARD_DIR = ".insertplay"
CONFIG_NAME = "portable-saves.json"
MODES = ("off", "userdata", "prefix")
USERS = os.path.join("pfx", "drive_c", "users")
STEAMUSER = os.path.join(USERS, "steamuser")
# precisa de symlink e permissões Unix (dosdevices, `My Documents` → `Documents`)
SUPPORTED_FS = {"ext4", "ext3", "btrfs", "xfs", "f2fs"}


def config_path(mount: str) -> str:
    return os.path.join(mount, CARD_DIR, CONFIG_NAME)


def compatdata(steam_root: str) -> str:
    return os.path.join(steam_root, "steamapps", "compatdata")


def get_mode(mount: str) -> str:
    data = _load(config_path(mount), {})
    mode = data.get("mode") if isinstance(data, dict) else None
    return mode if mode in MODES else "off"


def _write_mode(mount: str, mode: str, version: str) -> None:
    # minVersion: só diagnóstico. Uma versão sem o recurso ignora a pasta inteira.
    _save(config_path(mount), {"mode": mode, "minVersion": version})


class Layout:
    """Onde ficam os dados de um modo: `card` no cartão, `ssd` o caminho que vira link e
    `inner` a parte de um prefixo inteiro que corresponde a eles."""

    def __init__(self, mode: str, folder: str, inner: str):
        self.mode, self.folder, self.inner = mode, folder, inner
        self.mark = os.sep + os.path.join(CARD_DIR, folder) + os.sep

    def card(self, mount: str, appid: int) -> str:
        base = os.path.join(mount, CARD_DIR, self.folder, str(appid))
        return os.path.join(base, "steamuser") if self.inner else base

    def ssd(self, steam_root: str, appid: int) -> str:
        base = os.path.join(compatdata(steam_root), str(appid))
        return os.path.join(base, self.inner) if self.inner else base

    def valid(self, path: str) -> bool:
        if os.path.islink(path) or not os.path.isdir(path):
            return False
        return bool(self.inner) or os.path.isdir(os.path.join(path, "pfx"))

    def users_dir(self, path: str) -> str:
        """Pasta onde procurar dados de usuário (para escolher o mais recente)."""
        return path if self.inner else os.path.join(path, USERS)


LAYOUTS = {
    "userdata": Layout("userdata", "users", STEAMUSER),
    "prefix": Layout("prefix", "prefixes", ""),
}


def card_prefix(mount: str, appid: int) -> str:
    return LAYOUTS["prefix"].card(mount, appid)


def card_userdata(mount: str, appid: int) -> str:
    return LAYOUTS["userdata"].card(mount, appid)


def _unescape(field: str) -> str:
    """`/proc/mounts` escreve espaço como `\\040` (`/run/media/deck/Call\\040of\\040Juarez\\040G`).
    Uma passada só: `\\134040` é barra invertida seguida de "040", não espaço."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def _mount_entry(mount: str, mounts_file: str):
    """(sistema de arquivos, opções) do ponto de montagem mais específico que contém `mount`."""
    real = os.path.realpath(mount)
    best, entry = "", (None, ())
    try:
        with open(mounts_file, encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 4:
                    continue
                point = _unescape(parts[1])
                inside = real == point or real.startswith(point.rstrip(os.sep) + os.sep)
                if inside and len(point) > len(best):
                    best, entry = point, (parts[2], tuple(parts[3].split(",")))
    except OSError:
        pass
    return entry


def fs_type(mount: str, mounts_file: str = "/proc/mounts"):
    """Sistema de arquivos do ponto de montagem mais específico que contém `mount`."""
    return _mount_entry(mount, mounts_file)[0]


def status(mount: str, mounts_file: str = "/proc/mounts") -> dict:
    kind, options = _mount_entry(mount, mounts_file)
    reason = None
    if kind not in SUPPORTED_FS:
        reason = f"o cartão é {kind or 'desconhecido'}; precisa ser ext4 (formatado pelo Steam)"
    elif "ro" in options or "emergency_ro" in options:
        # Medido no Deck: ext4 com erro (bitmap com checksum errado) é remontado só
        # leitura segundos depois de montar; `/proc/mounts` mostra `emergency_ro`.
        reason = "o cartão está somente leitura (erro no sistema de arquivos; rode e2fsck)"
    return {"mode": get_mode(mount), "fsOk": reason is None, "fsType": kind, "reason": reason}


def _user_data_mtime(users: str):
    """mtime mais recente dos arquivos de usuário (fora de `AppData/Local/Temp` e
    `AppData/Local/dxvk`), ou None
    se não há nenhum. Medido: o prefixo que o Steam cria num move ou instalação não tem
    arquivo nenhum ali; o de um jogo jogado tem o save ou as configurações. Sem isso, o
    prefixo vazio recém-criado venceria o save por ser o mais novo."""
    newest = None
    for root, dirs, files in os.walk(users):
        # `Temp` e o cache do DXVK (`AppData/Local/dxvk/*.dxvk.bin`, regravado a cada
        # execução) não são do usuário: com eles, o aparelho que rodou por último venceria
        # mesmo sem save nenhum. Continuam sendo copiados, só não contam.
        if os.path.basename(root) in ("Temp", "dxvk") and os.path.basename(os.path.dirname(root)) == "Local":
            dirs[:] = []
            continue
        for name in files:
            try:
                st = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            # link (`My Documents` → `Documents` quebrado) não é dado de usuário
            if not stat.S_ISREG(st.st_mode):
                continue
            if newest is None or st.st_mtime > newest:
                newest = st.st_mtime
    return newest


def _free_name(path: str) -> str:
    candidate, n = path, 1
    while os.path.lexists(candidate):
        n += 1
        candidate = f"{path}-{n}"
    return candidate


def _stamp(now: float) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(now))


def _ssd_backup(path: str, now: float) -> str:
    target = _free_name(f"{path}.insertplay-backup-{_stamp(now)}")
    os.rename(path, target)
    return target


def _card_backup(mount: str, path: str, appid: int, now: float) -> str:
    folder = os.path.join(mount, CARD_DIR, "backups")
    os.makedirs(folder, exist_ok=True)
    target = _free_name(os.path.join(folder, f"{appid}-{_stamp(now)}"))
    os.rename(path, target)
    return target


def _copy(src: str, dest: str) -> None:
    """Cópia entre discos que nunca deixa `dest` pela metade: copia para `.partial` e
    renomeia no fim. Um `.partial` velho (cópia interrompida) é descartado: é cópia nossa,
    a origem continua inteira."""
    partial = dest + ".partial"
    if os.path.lexists(partial):
        shutil.rmtree(partial)
    shutil.copytree(src, partial, symlinks=True)
    os.rename(partial, dest)


def live_appids(proc_root: str = "/proc") -> set:
    """Appids com processo vivo agora. O Steam põe `SteamAppId=<appid>` no ambiente dos
    processos do jogo (medido no `environ` do Proton do Bully na Steam Machine). A lista
    do frontend é tirada no começo da passada; isto vale na hora de cada troca."""
    found = set()
    try:
        pids = [n for n in os.listdir(proc_root) if n.isdigit()]
    except OSError:
        return found
    for pid in pids:
        try:
            with open(os.path.join(proc_root, pid, "environ"), "rb") as f:
                env = f.read()
        except OSError:  # processo de outro usuário ou que acabou de sair
            continue
        for var in env.split(b"\0"):
            if var.startswith(b"SteamAppId="):
                value = var[len(b"SteamAppId="):]
                if value.isdigit() and int(value) > 0:
                    found.add(int(value))
    return found


# trocado nos testes
LIVE_APPIDS = live_appids


def _running(appid: int, running=()) -> bool:
    return appid in running or appid in LIVE_APPIDS()


def _ours(link: str, layout: Layout) -> bool:
    return os.path.islink(link) and layout.mark in os.readlink(link)


def _link(ssd: str, dest: str) -> None:
    if os.path.islink(ssd):
        os.remove(ssd)  # só o link; o destino fica
    os.makedirs(os.path.dirname(ssd), exist_ok=True)
    os.symlink(dest, ssd)


def _leftovers(libraries: list, appid: int) -> list:
    """O move SSD → cartão leva `compatdata/<appid>` para a biblioteca do cartão (seguindo
    nossos links). Essa cópia bloqueia o move de volta (issue #13)."""
    return [os.path.join(lib, "steamapps", "compatdata", str(appid)) for lib in libraries]


def sync_app(layout: Layout, mount: str, libraries: list, appid: int, steam_root: str, now: float,
             running=()) -> str:
    """Deixa o caminho do SSD (`layout.ssd`) apontando para o cartão.

    Candidatos: o que já está no cartão, a parte correspondente das cópias que um move
    deixa em `steamapps/compatdata` e a pasta real no SSD. Vence quem tem dados de
    usuário mais recentes; sem dados em nenhum, o do cartão. Os outros viram backup."""
    ssd = layout.ssd(steam_root, appid)
    dest = layout.card(mount, appid)
    lefts = [p for p in _leftovers(libraries, appid) if os.path.isdir(p) and not os.path.islink(p)]
    if os.path.islink(ssd) and not _ours(ssd, layout):
        return "foreign"
    # `compatdata/<appid>` já é link (modo prefixo, outro cartão, alguém): o caminho de
    # `steamuser` cairia dentro do destino dele
    if layout.inner and os.path.islink(os.path.join(compatdata(steam_root), str(appid))):
        return "foreign"
    # o contrário: prefixo inteiro cujo `steamuser` é link de outro cartão (modo userdata).
    # Copiado com os links, os saves deste cartão iriam para o outro.
    if not layout.inner and os.path.islink(os.path.join(ssd, STEAMUSER)):
        return "foreign"
    if os.path.islink(ssd) and os.readlink(ssd) == dest and layout.valid(dest):
        # Com o link no lugar, a cópia em `steamapps/compatdata` costuma ser a que o move
        # SSD → cartão fez seguindo o link: sai para os backups. Mas o Steam do modo desktop
        # usa justamente essa pasta. Se ela tem save mais novo que o do cartão, decide a
        # escolha do mais recente, com aviso de conflito.
        current = _user_data_mtime(layout.users_dir(dest))
        newer = False
        for left in lefts:
            inner = os.path.join(left, layout.inner) if layout.inner else left
            m = _user_data_mtime(layout.users_dir(inner)) if layout.valid(inner) else None
            if m is not None and (current is None or m > current):
                newer = True
        if not newer:
            for path in lefts:
                _card_backup(mount, path, appid, now)
            return "leftover" if lefts else "ok"

    candidates = []  # (caminho, onde) em ordem de preferência sem dados
    if layout.valid(dest):
        candidates.append((dest, "card"))
    for left in lefts:
        inner = os.path.join(left, layout.inner) if layout.inner else left
        if layout.valid(inner):
            candidates.append((inner, "leftover"))
    ssd_real = os.path.isdir(ssd) and not os.path.islink(ssd)
    if ssd_real and layout.valid(ssd):
        candidates.append((ssd, "ssd"))
    if not candidates:
        return "none"

    scored = [(path, where, _user_data_mtime(layout.users_dir(path))) for path, where in candidates]
    with_data = [s for s in scored if s[2] is not None]
    winner, where, newest = max(with_data, key=lambda s: s[2]) if with_data else scored[0]
    # Perdedor com dados de usuário de outra data = havia outro save. Sem dados, ou com a
    # mesma data mais recente (uma cópia do mesmo save), o backup é silencioso.
    conflict = any(m is not None and m != newest for path, _, m in scored if path != winner)

    # o frontend mandou a lista de jogos abertos no começo da passada; confere de novo
    # antes da primeira mudança
    if _running(appid, running):
        return "running"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    # destino que não venceu sai do caminho, válido ou não (pasta sem `pfx`, cópia
    # parcial): o rename por cima dele falharia em toda passada
    if where != "card" and os.path.lexists(dest):
        _card_backup(mount, dest, appid, now)
    if where == "ssd":
        _copy(ssd, dest)
        # A cópia do prefixo inteiro num cartão lento leva minutos. Se o jogo abriu no
        # meio e gravou, a cópia já está velha: descartamos (é nossa) e a próxima passada,
        # com o jogo fechado, tenta de novo. A pasta do SSD fica intacta.
        if _user_data_mtime(layout.users_dir(ssd)) != newest:
            shutil.rmtree(dest)
            return "changed"
        if _running(appid, running):  # abriu durante a cópia: a pasta do SSD está em uso
            shutil.rmtree(dest)
            return "running"
    if where == "leftover":
        os.rename(winner, dest)
    for left in lefts:
        if os.path.lexists(left):
            _card_backup(mount, left, appid, now)
    # A pasta do SSD sai do caminho do link. Se ela venceu, já está copiada no cartão e
    # fica como backup no SSD; se perdeu, também. Pasta inválida (o Steam cria só o
    # `config_info` em alguns passos) também é guardada, nunca apagada.
    if ssd_real:
        _ssd_backup(ssd, now)
    # userdata sem prefixo no aparelho (o jogo nunca rodou aqui): cria só o caminho até
    # `users/`. Medido: o Proton monta o prefixo em volta do link e o save já aparece na
    # primeira abertura.
    _link(ssd, dest)
    if conflict:
        return "conflict"
    return "migrated" if where != "card" else "linked"


def _links_to(steam_root: str, mount: str) -> dict:
    """`{appid: (layout, link)}` dos nossos symlinks que apontam para este cartão."""
    folder = compatdata(steam_root)
    real = os.path.realpath(mount)
    out = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for name in names:
        if not name.isdigit():
            continue
        for layout in LAYOUTS.values():
            link = layout.ssd(steam_root, int(name))
            base = os.path.join(real, CARD_DIR, layout.folder) + os.sep
            if _ours(link, layout) and os.readlink(link).startswith(base):
                out[int(name)] = (layout, link)
    return out


def _restore(link: str, src: str, layout: Layout, appid: int, running=()) -> str:
    """Troca o link por uma cópia do que está no cartão (que fica intacto). O link só sai
    depois de a cópia estar inteira ao lado e conferida: se o jogo abriu ou gravou durante
    a cópia, ela é descartada e o link fica. Devolve `restored`, `running` ou `changed`."""
    if _running(appid, running):
        return "running"
    staged = link + ".insertplay-restore"
    # uma restauração interrompida entre a cópia e a troca deixa a cópia pronta aqui; ela
    # vira backup em vez de travar toda tentativa seguinte
    if os.path.lexists(staged):
        os.rename(staged, _free_name(staged + ".old"))
    before = _user_data_mtime(layout.users_dir(src))
    _copy(src, staged)
    if _user_data_mtime(layout.users_dir(src)) != before or _running(appid, running):
        shutil.rmtree(staged)
        return "changed"
    os.remove(link)
    os.rename(staged, link)
    return "restored"


def sync(mount: str, libraries: list, appids: list, running: list, elsewhere: list, steam_root: str, now=None) -> dict:
    """Uma passada do save portátil num cartão. Desligado, não faz nada. Devolve
    `{appid: resultado}`.

    - appid do cartão: `sync_app` no modo do cartão;
    - link nosso para este cartão de um appid que saiu dele: movido para outra
      biblioteca (`elsewhere`), vira cópia no SSD para o jogo continuar com o save;
      desinstalado, só perde o link. O cartão nunca é apagado."""
    mode = get_mode(mount)
    if mode == "off":
        # Desligado em outro aparelho: os links que este aparelho ainda tem para o cartão
        # viram cópia, senão o jogo seguiria gravando no cartão e, sem o cartão, o save
        # sumiria deste aparelho. Sem links, não faz nada.
        return _release_all(mount, running, steam_root)
    layout = LAYOUTS[mode]
    now = time.time() if now is None else now
    running = set(running) | LIVE_APPIDS()
    elsewhere = set(elsewhere)
    out = {}
    # Modo trocado em outro aparelho ("Saves" <-> "Prefixo completo"): o link do modo
    # antigo deste aparelho volta a ser cópia antes de aplicar o novo. Sem isso o novo modo
    # via o link velho como alheio (`foreign`) e o jogo seguia nos dados antigos.
    for appid, (kind, link) in sorted(_links_to(steam_root, mount).items()):
        if kind is layout:
            continue
        try:
            src = kind.card(mount, appid)
            result = _restore(link, src, kind, appid, running) if kind.valid(src) else "unlinked"
            if result == "unlinked":
                os.remove(link)
        except OSError as exc:
            result = f"error: {exc}"
        if result != "restored":
            out[appid] = result  # jogo aberto ou falha: o novo modo espera esse jogo
    for appid in sorted(set(appids)):
        if appid in out:
            continue
        if appid in running:
            out[appid] = "running"
            continue
        try:
            out[appid] = sync_app(layout, mount, libraries, appid, steam_root, now, running)
        except OSError as exc:
            out[appid] = f"error: {exc}"
    for appid, (kind, link) in sorted(_links_to(steam_root, mount).items()):
        if appid in out or appid in running:
            continue
        try:
            src = kind.card(mount, appid)
            if appid in elsewhere and kind.valid(src):
                out[appid] = _restore(link, src, kind, appid, running)
            else:
                os.remove(link)
                out[appid] = "unlinked"
        except OSError as exc:
            out[appid] = f"error: {exc}"
    return out


def _release_all(mount: str, running: list, steam_root: str) -> dict:
    """Cada link para o cartão vira cópia no SSD. Com um jogo do cartão aberto, não mexe
    em nada (devolve `running` para ele)."""
    links = _links_to(steam_root, mount)
    running = set(running) | LIVE_APPIDS()
    busy = sorted(set(links) & running)
    if busy:
        return {appid: "running" for appid in busy}
    out = {}
    for appid, (layout, link) in sorted(links.items()):
        src = layout.card(mount, appid)
        try:
            if layout.valid(src):
                out[appid] = _restore(link, src, layout, appid, running)
            else:
                os.remove(link)
                out[appid] = "unlinked"
        except OSError as exc:
            out[appid] = f"error: {exc}"
    return out


def set_mode(mount: str, mode: str, libraries: list, appids: list, running: list, elsewhere: list,
             steam_root: str, version: str, now=None) -> dict:
    """Troca o modo do cartão. Sair de um modo copia de volta para o SSD o que estava no
    cartão; entrar num modo já faz a primeira passada. Se um jogo do cartão estiver aberto,
    nada muda (resultado `running`)."""
    if mode not in MODES:
        raise ValueError(f"unknown portable mode: {mode!r}")
    current = get_mode(mount)
    if current == mode:
        # escolher de novo o modo ativo não é troca: só uma passada
        return sync(mount, libraries, appids, running, elsewhere, steam_root, now)
    results = {}
    if current != "off":
        results = _release_all(mount, running, steam_root)
        # jogo aberto ou cópia de volta que falhou: o modo fica, para tentar de novo
        if any(r in ("running", "changed") or r.startswith("error") for r in results.values()):
            return results
    _write_mode(mount, mode, version)
    if mode != "off":
        results.update(sync(mount, libraries, appids, running, elsewhere, steam_root, now))
    return results


def unlink_dangling(steam_root: str, running=()) -> list:
    """Tira os nossos links cujo cartão não está montado (link quebrado). Medido:
    inofensivo para o Steam, mas atrapalharia reinstalar o jogo no SSD com o cartão fora.
    Jogo aberto fica com o link: o cartão pode voltar, e a passada normal não mexe em jogo
    aberto para refazê-lo."""
    running = set(running) | LIVE_APPIDS()
    folder = compatdata(steam_root)
    removed = []
    try:
        names = os.listdir(folder)
    except OSError:
        return removed
    for name in sorted(names):
        if not name.isdigit() or int(name) in running:
            continue
        for layout in LAYOUTS.values():
            link = layout.ssd(steam_root, int(name))
            if _ours(link, layout) and not os.path.exists(link):
                os.remove(link)
                removed.append(int(name))
    return removed
