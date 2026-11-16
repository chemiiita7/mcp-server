"""tools/minecraft.py - Herramientas MCP para el servidor Minecraft KOGANE.

Cargar con el cargador dinámico (expone register_tools(mcp)).

Variables de entorno (todas opcionales salvo la contraseña RCON):
  MC_DIR              carpeta de datos del server       (def. /minecraft/data)
  MC_BACKUP_DIR       carpeta de copias de seguridad    (def. /minecraft/backups)
  MC_RCON_HOST/PORT   RCON                              (def. 127.0.0.1 / 25575)
  MC_RCON_PASSWORD    contraseña RCON
  MC_GAME_PORT        puerto de juego, para detectar si está encendido (def. 25565)
  MC_CONTAINER        nombre del contenedor de Minecraft (def. minecraft)
  DOCKER_HOST         proxy de Docker, ej. tcp://127.0.0.1:2375 (recomendado)
  DOCKER_SOCK         alternativa: socket unix directo  (def. /var/run/docker.sock)
"""
import http.client
import json
import os
import re
import socket
import struct
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

MC_DIR = Path(os.environ.get("MC_DIR", "/minecraft/data"))
BACKUP_DIR = Path(os.environ.get("MC_BACKUP_DIR", "/minecraft/backups"))
RCON_HOST = os.environ.get("MC_RCON_HOST", "127.0.0.1")
RCON_PORT = int(os.environ.get("MC_RCON_PORT", "25575"))
RCON_PASSWORD = os.environ.get("MC_RCON_PASSWORD", "")
GAME_PORT = int(os.environ.get("MC_GAME_PORT", "25565"))
MC_CONTAINER = os.environ.get("MC_CONTAINER", "minecraft")
DOCKER_SOCK = os.environ.get("DOCKER_SOCK", "/var/run/docker.sock")
DOCKER_HOST = os.environ.get("DOCKER_HOST", "")
STOP_GRACE = 60  # segundos que Docker espera al guardado antes de matar el proceso

PROPS_FILE = MC_DIR / "server.properties"
WHITELIST_FILE = MC_DIR / "whitelist.json"

# Claves que esta tool se niega a tocar (podrían dejarte sin acceso o abrir el server).
PROTECTED_PROPS = {"rcon.password", "rcon.port", "enable-rcon", "online-mode", "server-port"}
# Formato antiguo (hasta 1.21.x): overworld en la raíz del mundo, DIM-1 / DIM1.
# Formato nuevo (desde 26.1): dimensions/minecraft/<dim>/ y players/{data,stats,advancements}.
DIMENSIONS = {"overworld": "", "nether": "DIM-1", "end": "DIM1"}
DIMENSIONS_NEW = {"overworld": "dimensions/minecraft/overworld",
                  "nether": "dimensions/minecraft/the_nether",
                  "end": "dimensions/minecraft/the_end"}
REGION_FOLDERS = ("region", "entities", "poi")  # 1.17+ guarda entidades y POI aparte
MAX_REGIONS = 64


# --------------------------------------------------------------------------- RCON
def _rcon(command: str, timeout: float = 5.0) -> str:
    """Cliente RCON mínimo (sin dependencias). Lanza OSError si no hay conexión."""
    if not RCON_PASSWORD:
        raise OSError("MC_RCON_PASSWORD no configurada")

    def send(sock, req_id, ptype, body):
        data = body.encode() + b"\x00"
        sock.sendall(struct.pack("<iii", len(data) + 9, req_id, ptype) + data + b"\x00")

    def recv(sock):
        raw = b""
        while len(raw) < 4:
            chunk = sock.recv(4 - len(raw))
            if not chunk:
                raise OSError("RCON cerrado")
            raw += chunk
        size = struct.unpack("<i", raw)[0]
        payload = b""
        while len(payload) < size:
            chunk = sock.recv(size - len(payload))
            if not chunk:
                raise OSError("RCON cerrado")
            payload += chunk
        req_id, ptype = struct.unpack("<ii", payload[:8])
        return req_id, ptype, payload[8:-2].decode(errors="replace")

    with socket.create_connection((RCON_HOST, RCON_PORT), timeout=timeout) as s:
        send(s, 1, 3, RCON_PASSWORD)
        rid, _, _ = recv(s)
        if rid == -1:
            raise OSError("RCON: contraseña incorrecta")
        send(s, 2, 2, command)
        _, _, out = recv(s)
        return out


def _rcon_ok() -> bool:
    try:
        _rcon("list")
        return True
    except OSError:
        return False


def _game_port_open() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", GAME_PORT), timeout=1.5):
            return True
    except OSError:
        return False


class _UnixConn(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


def _docker(method: str, action: str, timeout: float = STOP_GRACE + 30):
    """Llama a la API de Docker, SOLO sobre el contenedor MC_CONTAINER."""
    if DOCKER_HOST.startswith("tcp://"):
        host, _, port = DOCKER_HOST[6:].partition(":")
        conn = http.client.HTTPConnection(host, int(port or 2375), timeout=timeout)
    else:
        conn = _UnixConn(DOCKER_SOCK, timeout)
    try:
        conn.request(method, f"/containers/{MC_CONTAINER}/{action}")
        r = conn.getresponse()
        body = r.read().decode(errors="replace")
        return r.status, body
    finally:
        conn.close()


def _server_running() -> bool:
    return _game_port_open() or _rcon_ok()


# ----------------------------------------------------------------- server.properties
def _read_props() -> dict:
    props = {}
    if PROPS_FILE.exists():
        for line in PROPS_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                props[k.strip()] = v
    return props


def _level_name() -> str:
    return _read_props().get("level-name", "world")


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ----------------------------------------------------------------------- regiones
def _parse_regions(regions, block_area):
    found = set()
    for item in regions or []:
        m = re.fullmatch(r"\s*(?:r\.)?(-?\d+)[.,\s]+(-?\d+)(?:\.mca)?\s*", str(item))
        if not m:
            raise ValueError(f"Región no válida: {item!r} (usa 'r.-1.2.mca' o '-1,2')")
        found.add((int(m.group(1)), int(m.group(2))))
    if block_area:
        parts = [p for p in re.split(r"[,\s]+", block_area.strip()) if p]
        if len(parts) != 4:
            raise ValueError("block_area debe ser 'x1,z1,x2,z2' (coordenadas de bloque)")
        x1, z1, x2, z2 = (int(p) for p in parts)
        for rx in range(min(x1, x2) >> 9, (max(x1, x2) >> 9) + 1):
            for rz in range(min(z1, z2) >> 9, (max(z1, z2) >> 9) + 1):
                found.add((rx, rz))
    if not found:
        raise ValueError("Indica al menos una región (regions o block_area)")
    if len(found) > MAX_REGIONS:
        raise ValueError(f"Demasiadas regiones ({len(found)}); máximo {MAX_REGIONS} por llamada")
    return sorted(found)


def _backup_path(name: str) -> Path:
    if not name or "/" in name or "\\" in name or ".." in name:
        raise ValueError("Nombre de copia no válido")
    p = BACKUP_DIR / name
    if not p.is_file():
        raise FileNotFoundError(f"No existe la copia {name} en {BACKUP_DIR}")
    return p


def _tar_index(tar: tarfile.TarFile, level: str):
    """Devuelve (prefijo_del_mundo, {ruta_normalizada: miembro}).
    Funciona con copias del mundo solo ('world/…') y con copias de todo el directorio
    de datos ('./KOGANE/…'): localiza la carpeta del mundo por su level.dat."""
    members = {}
    for m in tar.getmembers():
        members[re.sub(r"^(\./)+", "", m.name)] = m
    cands = [n[:-len("level.dat")] for n in members if n == "level.dat" or n.endswith("/level.dat")]
    if not cands:
        raise ValueError("no encuentro level.dat en la copia (¿no contiene un mundo?)")
    cands.sort(key=lambda p: (p.rstrip("/").split("/")[-1] != level, p.count("/")))
    return cands[0], members


def _is_new_layout_tar(members: dict, wp: str) -> bool:
    return any(n.startswith(wp + "dimensions/") for n in members)


def _dim_sub(dimension: str, new: bool) -> str:
    """Subcarpeta de la dimensión dentro del mundo, con barra final ('' para el overworld antiguo)."""
    if dimension not in DIMENSIONS:
        raise ValueError(f"dimension debe ser una de {list(DIMENSIONS)}")
    sub = (DIMENSIONS_NEW if new else DIMENSIONS)[dimension]
    return sub + "/" if sub else ""


def _dim_prefix(wp: str, dimension: str, new: bool) -> str:
    """wp = prefijo del mundo dentro del tar (con barra final, p. ej. 'KOGANE/' o '')."""
    return wp + _dim_sub(dimension, new)


def _player_rels(uuid: str, new: bool, stats: bool, advancements: bool) -> list:
    base = "players/" if new else ""
    rels = [f"{base}{'data' if new else 'playerdata'}/{uuid}.dat"]
    if stats:
        rels.append(f"{base}stats/{uuid}.json")
    if advancements:
        rels.append(f"{base}advancements/{uuid}.json")
    return rels


def _layout_mismatch(tar_new: bool, level_dir: Path):
    live_new = (level_dir / "dimensions").is_dir()
    if tar_new != live_new:
        f = lambda n: "nuevo (26.1+)" if n else "antiguo (<26.1)"
        return (f"ERROR: la copia usa el formato de mundo {f(tar_new)} y el mundo actual el "
                f"{f(live_new)}; no se pueden mezclar. Usa una copia hecha tras la actualización.")
    return None


def _normal_backups():
    """Copias normales (no pre-restore, no .part), de más reciente a más antigua."""
    rx = re.compile(r"(?!pre-restore-)[\w.]+(-[\w.]+)*-\d{8}-\d{6}.*\.tar\.gz")
    return sorted((p for p in BACKUP_DIR.glob("*.tar.gz") if rx.fullmatch(p.name)),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def _uuid_for(player: str):
    """UUID y nombre exacto de un jugador, según usercache.json o la whitelist."""
    for f in (MC_DIR / "usercache.json", WHITELIST_FILE):
        try:
            data = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        for e in data:
            if str(e.get("name", "")).lower() == player.lower() and e.get("uuid"):
                return e["uuid"], e["name"]
    return None, None


def _online_players() -> list:
    out = _rcon("list")
    return [n.strip().lower() for n in out.partition(":")[2].split(",") if n.strip()]


def _stop_server() -> str:
    """Guarda el mundo y para el contenedor con margen para el guardado final."""
    try:
        _rcon("say El servidor se apaga en unos segundos")
        _rcon("save-all flush", timeout=60)
    except OSError:
        pass  # si RCON no responde, Docker enviará SIGTERM igualmente
    st, body = _docker("POST", f"stop?t={STOP_GRACE}")
    return f"HTTP {st}" + (f" {body[:200]}" if st >= 400 else "")


# ------------------------------------------------------------------------ registro
def register_tools(mcp):
    @mcp.tool()
    def minecraft_server(action: str = "status") -> str:
        """Controla el contenedor de Minecraft. action: 'status' | 'restart' | 'stop' | 'start'.
        restart/stop avisan a los jugadores, fuerzan un guardado (save-all flush) y dan hasta
        60 s a Docker para cerrar limpio. 'restart' no espera a que el mundo termine de cargar:
        usa 'status' / action=status tras ~1 min."""
        try:
            if action == "status":
                if _rcon_ok():
                    return f"{MC_CONTAINER}: encendido | " + _rcon("list")
                if _game_port_open():
                    try:
                        _rcon("list")
                        why = "sin error (reintenta)"
                    except OSError as e:
                        why = f"{type(e).__name__}: {e}"
                    return (f"{MC_CONTAINER}: puerto de juego abierto, pero RCON falla en "
                            f"{RCON_HOST}:{RCON_PORT} -> {why}")
                return f"{MC_CONTAINER}: apagado (o aún arrancando)"
            if action in ("restart", "stop"):
                msg = _stop_server()
                if action == "stop":
                    return f"Servidor parado ({msg})."
                st, body = _docker("POST", "start")
                return ("Reiniciando: servidor arrancando, tardará ~1 min en aceptar jugadores."
                        if st in (204, 304) else f"ERROR al arrancar ({st}): {body[:200]}")
            if action == "start":
                st, body = _docker("POST", "start")
                return "Servidor arrancando." if st in (204, 304) else f"ERROR ({st}): {body[:200]}"
            return "action debe ser 'status', 'restart', 'stop' o 'start'"
        except OSError as e:
            return f"ERROR: no puedo hablar con Docker en {DOCKER_HOST or DOCKER_SOCK} ({e})"

    @mcp.tool()
    def minecraft_backup(label: str = "") -> str:
        """Crea una copia de seguridad (tar.gz) del mundo de Minecraft.
        Si el servidor está encendido, pausa el guardado por RCON (save-off + save-all flush)
        para que la copia sea consistente y lo reactiva al terminar.
        label: etiqueta opcional para reconocer la copia (ej. 'antes-de-mod')."""
        level = _level_name()
        world = MC_DIR / level
        if not world.is_dir():
            return f"ERROR: no existe la carpeta del mundo {world}"
        rcon_ok = _rcon_ok()
        if _game_port_open() and not rcon_ok:
            return "ERROR: el servidor está encendido pero RCON no responde; copia no segura. Abortado."
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", label).strip("-")
        name = f"{level}-{time.strftime('%Y%m%d-%H%M%S')}{'-' + slug if slug else ''}.tar.gz"
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        final, tmp = BACKUP_DIR / name, BACKUP_DIR / (name + ".part")
        try:
            if rcon_ok:
                _rcon("save-off")
                _rcon("save-all flush", timeout=60)
            with tarfile.open(tmp, "w:gz") as tar:
                tar.add(world, arcname=level,
                        filter=lambda ti: None if ti.name.endswith("session.lock") else ti)
            os.replace(tmp, final)
        except Exception as e:
            if tmp.exists():
                tmp.unlink()
            return f"ERROR creando la copia: {e}"
        finally:
            if rcon_ok:
                try:
                    _rcon("save-on")
                except OSError:
                    pass
        mb = final.stat().st_size / 1024 / 1024
        return f"OK: {name} ({mb:.1f} MB) en {BACKUP_DIR}. Servidor {'en vivo' if rcon_ok else 'apagado'}."

    @mcp.tool()
    def minecraft_list_backups(backup: str = "", dimension: str = "overworld") -> str:
        """Lista las copias de seguridad disponibles. Si indicas 'backup' (nombre exacto),
        lista además las regiones (r.X.Z) que contiene para la dimensión elegida
        (overworld | nether | end). Región = 512x512 bloques; X = floor(x_bloque/512)."""
        if not backup:
            files = sorted(BACKUP_DIR.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not files:
                return f"No hay copias en {BACKUP_DIR}"
            return "\n".join(
                f"{p.name}  {p.stat().st_size / 1024 / 1024:.1f} MB  "
                f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(p.stat().st_mtime))}"
                for p in files)
        try:
            path = _backup_path(backup)
            with tarfile.open(path, "r:gz") as tar:
                wp, idx = _tar_index(tar, _level_name())
                prefix = _dim_prefix(wp, dimension, _is_new_layout_tar(idx, wp)) + "region/"
                regs = sorted(n[len(prefix):] for n in idx
                              if n.startswith(prefix) and n.endswith(".mca"))
        except Exception as e:
            return f"ERROR: {e}"
        return f"{len(regs)} regiones en {backup} ({dimension}):\n" + "\n".join(regs)

    @mcp.tool()
    def minecraft_prune_backups(keep: int = 3, include_pre_restore: bool = False,
                                dry_run: bool = True) -> str:
        """Borra copias antiguas y conserva solo las 'keep' más recientes (por fecha).
        Solo toca copias <mundo>-YYYYMMDD-HHMMSS*.tar.gz (las antiguas 'world-…' y las nuevas 'KOGANE-…'); los pre-restore-*.tar.gz (red de seguridad de las
        restauraciones) se ignoran salvo include_pre_restore=true, que los poda con la misma regla.
        dry_run=True (por defecto): solo muestra qué se borraría."""
        if keep < 1:
            return "keep debe ser al menos 1"
        # Copias normales: <nombre>-YYYYMMDD-HHMMSS[-etiqueta].tar.gz (world-… antiguas o KOGANE-… nuevas)
        pattern = (r"[\w.]+(-[\w.]+)*-\d{8}-\d{6}.*\.tar\.gz" if include_pre_restore
                   else r"(?!pre-restore-)[\w.]+(-[\w.]+)*-\d{8}-\d{6}.*\.tar\.gz")
        files = sorted(
            (p for p in BACKUP_DIR.glob("*.tar.gz") if re.fullmatch(pattern, p.name)),
            key=lambda p: p.stat().st_mtime, reverse=True)
        old = files[keep:]
        if not old:
            return f"Nada que borrar: hay {len(files)} copias y keep={keep}."
        mb = sum(p.stat().st_size for p in old) / 1024 / 1024
        lines = "\n".join(f"  {p.name}" for p in old)
        if dry_run:
            return (f"PLAN (dry_run): se borrarían {len(old)} copias ({mb:.0f} MB), "
                    f"conservando las {keep} más recientes:\n{lines}")
        for p in old:
            p.unlink()
        return f"OK: borradas {len(old)} copias ({mb:.0f} MB). Conservadas {keep}:\n" + \
            "\n".join(f"  {p.name}" for p in files[:keep])

    @mcp.tool()
    def minecraft_restore_player(
        player: str,
        backup: str = "",
        include_stats: bool = False,
        include_advancements: bool = False,
        dry_run: bool = True,
    ) -> str:
        """Devuelve a un jugador al estado que tenía en una copia de seguridad: inventario,
        armadura, cofre de ender, XP, vida, hambre y posición (playerdata/<uuid>.dat, o
        players/data/<uuid>.dat en mundos 26.1+).
        player: nombre exacto. backup: nombre de la copia; si se omite, la más reciente.
        include_stats / include_advancements: restaurar también estadísticas y logros.
        El jugador debe estar DESCONECTADO (el server sobrescribiría el fichero al salir), pero
        el servidor puede seguir encendido. Antes de aplicar guarda el estado actual del jugador
        en 'pre-restore-player-*.tar.gz'. dry_run=True (por defecto): solo muestra el plan.
        Ojo: todo lo ganado desde la copia se pierde, la posición vuelve a la de la copia y
        los objetos que soltó al morir pueden seguir en el suelo (riesgo de duplicarlos)."""
        if not re.fullmatch(r"\w{3,16}", player):
            return "Nombre de jugador no válido"
        uuid, name = _uuid_for(player)
        if not uuid:
            return (f"ERROR: no encuentro a {player} en usercache.json ni en la whitelist. "
                    "Comprueba el nombre exacto.")
        try:
            if backup:
                path = _backup_path(backup)
            else:
                backups = _normal_backups()
                if not backups:
                    return f"ERROR: no hay copias en {BACKUP_DIR}"
                path = backups[0]
            with tarfile.open(path, "r:gz") as tar:
                wp, members = _tar_index(tar, _level_name())
                level_dir = MC_DIR / _level_name()
                tar_new = _is_new_layout_tar(members, wp)
                bad = _layout_mismatch(tar_new, level_dir)
                if bad:
                    return bad
                rels = _player_rels(uuid, tar_new, include_stats, include_advancements)
                plan = [(rel, members.get(wp + rel), level_dir / rel) for rel in rels]
                if plan[0][1] is None:
                    return f"ERROR: {name} no tiene datos en la copia {path.name} (¿no había entrado aún?)"
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
                lines = "\n".join(
                    f"  {'SOBRESCRIBIR' if dest.exists() else 'CREAR' if mem else 'omitir (no está en la copia)'}: {rel}"
                    for rel, mem, dest in plan)
                header = f"{name} ({uuid}) desde {path.name} ({when}):\n{lines}"
                if dry_run:
                    return "PLAN (dry_run, no se ha tocado nada) " + header + \
                        "\n\nRepite con dry_run=false. El jugador debe estar desconectado."
                # el jugador no puede estar conectado
                if _game_port_open():
                    try:
                        if name.lower() in _online_players():
                            return f"ERROR: {name} está conectado. Expúlsalo (kick) y repite."
                    except OSError as e:
                        return f"ERROR: servidor encendido y no puedo comprobar si {name} está conectado ({e})."
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                safety = BACKUP_DIR / f"pre-restore-player-{name}-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"
                with tarfile.open(safety, "w:gz") as st:
                    for rel, mem, dest in plan:
                        if dest.exists():
                            st.add(dest, arcname=f"{level_dir.name}/{rel}")
                done = []
                for rel, mem, dest in plan:
                    if not mem:
                        continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    src = tar.extractfile(mem)
                    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".restore-")
                    with os.fdopen(fd, "wb") as out:
                        while chunk := src.read(1 << 20):
                            out.write(chunk)
                    os.replace(tmp, dest)
                    done.append(rel)
            return (f"OK: {name} restaurado desde {path.name} ({', '.join(done)}). "
                    f"Estado previo guardado en {safety.name}. Ya puede volver a entrar.")
        except Exception as e:
            return f"ERROR: {e}"

    @mcp.tool()
    def minecraft_properties(action: str = "get", changes: dict | None = None, key: str = "") -> str:
        """Lee o edita server.properties.
        action='get': devuelve todo (o solo 'key' si se indica).
        action='set': aplica 'changes' (dict clave->valor, ej. {"max-players": "12", "motd": "KOGANE"}).
        Hace copia del fichero antes de editar. Los cambios requieren reiniciar el servidor.
        Claves protegidas (rcon.*, enable-rcon, online-mode, server-port) no se pueden cambiar.
        Aviso: la imagen itzg regenera server.properties desde variables de entorno al arrancar;
        si una clave está definida allí en docker-compose, ese valor prevalece."""
        if action == "get":
            props = _read_props()
            if key:
                return f"{key}={props[key]}" if key in props else f"{key} no existe"
            return "\n".join(f"{k}={v}" for k, v in props.items() if k != "rcon.password")
        if action != "set":
            return "action debe ser 'get' o 'set'"
        if not changes:
            return "Falta 'changes'"
        for k, v in changes.items():
            if not re.fullmatch(r"[a-z0-9.\-]+", k):
                return f"Clave no válida: {k!r}"
            if k in PROTECTED_PROPS:
                return f"Clave protegida, no se modifica desde aquí: {k}"
            if "\n" in str(v) or "\r" in str(v):
                return f"Valor no válido para {k}"
        text = PROPS_FILE.read_text(encoding="utf-8")
        (MC_DIR / f"server.properties.bak-{time.strftime('%Y%m%d-%H%M%S')}").write_text(text, encoding="utf-8")
        lines, pending, report = text.splitlines(), dict(changes), []
        for i, line in enumerate(lines):
            if "=" in line and not line.lstrip().startswith("#"):
                k = line.split("=", 1)[0].strip()
                if k in pending:
                    old = line.split("=", 1)[1]
                    lines[i] = f"{k}={pending[k]}"
                    report.append(f"{k}: {old} -> {pending.pop(k)}")
        for k, v in pending.items():
            lines.append(f"{k}={v}")
            report.append(f"{k}: (nuevo) -> {v}")
        _atomic_write(PROPS_FILE, "\n".join(lines) + "\n")
        return "OK (reinicia el servidor para aplicar):\n" + "\n".join(report)

    @mcp.tool()
    def minecraft_whitelist(action: str = "list", player: str = "") -> str:
        """Gestiona la whitelist. action: 'list' | 'add' | 'remove'. player: nombre Java exacto.
        Con el servidor encendido usa RCON (aplica al instante). Si está apagado, edita
        whitelist.json resolviendo el UUID contra la API de Mojang.
        Nota: si en docker-compose hay WHITELIST=... con OVERRIDE_WHITELIST, se reescribe al arrancar."""
        data = json.loads(WHITELIST_FILE.read_text()) if WHITELIST_FILE.exists() else []
        if action == "list":
            return "\n".join(sorted(e["name"] for e in data)) or "(whitelist vacía)"
        if action not in ("add", "remove"):
            return "action debe ser 'list', 'add' o 'remove'"
        if not re.fullmatch(r"\w{3,16}", player):
            return "Nombre de jugador no válido (3-16 caracteres alfanuméricos o _)"
        if _rcon_ok():
            return _rcon(f"whitelist {action} {player}")
        # Servidor apagado: editar el fichero
        if action == "remove":
            new = [e for e in data if e["name"].lower() != player.lower()]
            if len(new) == len(data):
                return f"{player} no estaba en la whitelist"
            _atomic_write(WHITELIST_FILE, json.dumps(new, indent=2) + "\n")
            return f"OK: {player} eliminado (servidor apagado, efectivo al arrancar)"
        if any(e["name"].lower() == player.lower() for e in data):
            return f"{player} ya estaba en la whitelist"
        try:
            with urllib.request.urlopen(
                    f"https://api.mojang.com/users/profiles/minecraft/{player}", timeout=8) as r:
                prof = json.load(r)
        except Exception as e:
            return f"ERROR: no pude resolver el UUID de {player} ({e}). Enciende el servidor y reintenta."
        u = prof["id"]
        uuid = f"{u[:8]}-{u[8:12]}-{u[12:16]}-{u[16:20]}-{u[20:]}"
        data.append({"uuid": uuid, "name": prof["name"]})
        _atomic_write(WHITELIST_FILE, json.dumps(data, indent=2) + "\n")
        return f"OK: {prof['name']} ({uuid}) añadido (servidor apagado, efectivo al arrancar)"

    @mcp.tool()
    def minecraft_restore_regions(
        backup: str,
        regions: list[str] | None = None,
        block_area: str = "",
        dimension: str = "overworld",
        delete_if_missing: bool = False,
        dry_run: bool = True,
        manage_server: bool = False,
    ) -> str:
        """Restaura regiones concretas del mapa desde una copia de seguridad.
        backup: nombre exacto (ver minecraft_list_backups).
        regions: lista como ['r.0.0.mca', '-1,2'] (coordenadas de región).
        block_area: alternativa/complemento en bloques 'x1,z1,x2,z2' (se convierte a regiones).
        dimension: overworld | nether | end.
        delete_if_missing: si la región no existía en la copia, borrarla del mundo actual
          (vuelve a generarse de cero). Por defecto se deja como está.
        dry_run=True (por defecto): solo muestra el plan, no toca nada.
        Restaura también entities/ y poi/ de cada región. El servidor DEBE estar apagado
        (los chunks cargados en memoria sobrescribirían la restauración). Antes de aplicar,
        guarda el estado actual de esas regiones en una copia 'pre-restore-*.tar.gz'.
        manage_server=True: si está encendido, lo para (guardando antes), restaura y lo vuelve
        a arrancar. Si la restauración falla, se queda apagado y te lo indica."""
        try:
            wanted = _parse_regions(regions, block_area)
            path = _backup_path(backup)
            with tarfile.open(path, "r:gz") as tar:
                wp, members = _tar_index(tar, _level_name())
                level_dir = MC_DIR / _level_name()
                tar_new = _is_new_layout_tar(members, wp)
                bad = _layout_mismatch(tar_new, level_dir)
                if bad:
                    return bad
                prefix = _dim_prefix(wp, dimension, tar_new)
                dim_sub = _dim_sub(dimension, tar_new)
                dim_dir = level_dir / dim_sub

                plan = []  # (rel, member|None, destino)
                for rx, rz in wanted:
                    for folder in REGION_FOLDERS:
                        rel = f"{folder}/r.{rx}.{rz}.mca"
                        plan.append((rel, members.get(prefix + rel), dim_dir / rel))

                lines = []
                for rel, mem, dest in plan:
                    if mem and dest.exists():
                        act = "SOBRESCRIBIR"
                    elif mem:
                        act = "CREAR"
                    elif dest.exists():
                        act = "BORRAR" if delete_if_missing else "dejar (no está en la copia)"
                    else:
                        continue
                    lines.append(f"  {act}: {dimension}/{rel}")
                if not lines:
                    return "Ninguna de esas regiones existe ni en la copia ni en el mundo actual."

                if dry_run:
                    return (f"PLAN (dry_run, no se ha tocado nada) desde {backup}:\n" + "\n".join(lines)
                            + "\n\nRepite con dry_run=false y el servidor apagado para aplicar.")

                stopped_by_us = False
                if _server_running():
                    if not manage_server:
                        return ("ERROR: el servidor está encendido. Apágalo (minecraft_server stop) "
                                "o repite con manage_server=true.")
                    msg = _stop_server()
                    if _server_running():
                        return f"ERROR: no pude apagar el servidor ({msg})."
                    stopped_by_us = True

                # copia de seguridad de lo que se va a pisar
                safety = BACKUP_DIR / f"pre-restore-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                with tarfile.open(safety, "w:gz") as st:
                    for rel, mem, dest in plan:
                        if dest.exists():
                            st.add(dest, arcname=f"{level_dir.name}/{dim_sub}{rel}")

                done = 0
                for rel, mem, dest in plan:
                    if mem:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        src = tar.extractfile(mem)
                        fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".restore-")
                        with os.fdopen(fd, "wb") as out:
                            while chunk := src.read(1 << 20):
                                out.write(chunk)
                        os.replace(tmp, dest)
                        done += 1
                    elif dest.exists() and delete_if_missing:
                        dest.unlink()
                        done += 1
            tail = "Ya puedes arrancar el servidor."
            if stopped_by_us:
                st, body = _docker("POST", "start")
                tail = "Servidor arrancado de nuevo." if st in (204, 304) else f"ERROR al arrancar ({st}): {body[:200]}"
            return (f"OK: {done} ficheros restaurados desde {backup}. "
                    f"Estado previo guardado en {safety.name}. {tail}")
        except Exception as e:
            return f"ERROR: {e}"
