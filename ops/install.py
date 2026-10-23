#!/usr/bin/env python3
"""Interactive Ubuntu installer. Author: Arahort <alex@arahort.pro>."""
import argparse
import getpass
import grp
import ipaddress
import json
import os
import pathlib
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import urllib.parse
from host import Host, REPOSITORY, VERSION, atomic, read_env, set_env
SOURCE = pathlib.Path(__file__).resolve().parent.parent
def release_repository(source, local=False):
    path = pathlib.Path(source) / "release-source.json"
    try:
        if path.stat().st_size > 4096:
            raise ValueError("Слишком большой release-source.json")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("В дистрибутиве отсутствует или повреждён release-source.json автора") from error
    if not isinstance(data, dict) or set(data) != {"format", "repository"} or type(data["format"]) is not int or data["format"] != 1 or not isinstance(data["repository"], str):
        raise ValueError("Неподдерживаемый release-source.json автора")
    repository = data["repository"]
    if repository and not REPOSITORY.fullmatch(repository):
        raise ValueError("Некорректный репозиторий автора в release-source.json")
    if not repository and not local:
        raise ValueError("Дистрибутив ещё не привязан к репозиторию автора. Для локального теста используй --local")
    return repository
def ask(label, default=None):
    value = input(label + (f" [{default}]" if default is not None else "") + ": ").strip()
    return value or default or ""
def yes(label, default=False):
    while True:
        answer = ask(label + " (да/нет)", "да" if default else "нет").lower()
        if answer in ("да", "д", "yes", "y"):
            return True
        if answer in ("нет", "н", "no", "n"):
            return False
        print("Ответь да или нет.")
def proxy_question():
    russia = yes("Сервер находится в РФ?")
    if not russia and not yes("Использовать HTTP(S) прокси для AGY?"):
        return ""
    print("Нужен HTTP(S) CONNECT прокси с доступом к Google; пароль вводится скрыто.")
    while True:
        scheme = ask("Протокол прокси", "http").lower()
        host = ask("Хост/IP прокси без протокола и порта")
        port = ask("Порт прокси")
        if scheme not in ("http", "https") or not host or any(c in host for c in "/@?# \n\r") or not port.isdigit() or not 1 <= int(port) <= 65535:
            print("Проверь протокол, хост и порт.")
            continue
        login = ask("Логин прокси (пусто без авторизации)")
        password = getpass.getpass("Пароль прокси: ") if login else ""
        if any(c in login + password for c in "\n\r\x00"):
            print("Недопустимые символы.")
            continue
        if ":" in host and not host.startswith("["):
            host = "[" + host + "]"
        auth = urllib.parse.quote(login, safe="") + ":" + urllib.parse.quote(password, safe="") + "@" if login else ""
        return f"{scheme}://{auth}{host}:{port}"
def admin_question():
    while True:
        name = ask("Логин администратора")
        if not re.fullmatch(r"[A-Za-z0-9_А-Яа-яЁё.@+-]{3,64}", name):
            print("От 3 до 64 букв, цифр или символов _.@+-.")
            continue
        password = getpass.getpass("Пароль администратора (минимум 12 символов): ")
        repeat = getpass.getpass("Повтори пароль: ")
        if len(password) < 12 or password != repeat or any(c in password for c in "\r\n\x00"):
            print("Пароли должны совпадать, минимум 12 символов.")
            continue
        return name, password
def port_available(port, bind="0.0.0.0"):
    sock = socket.socket()
    try:
        sock.bind((bind, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()
def network_question():
    while True:
        mode = ask("Доступ: 1 — домен и автоматический HTTPS, 2 — IP/локальная сеть HTTP, 3 — существующий обратный прокси", "2")
        if mode == "1":
            domain = ask("Домен без https:// и пути").lower()
            if len(domain) > 253 or "." not in domain or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in domain.split(".")):
                print("Укажи доменное имя, например game.example.org.")
                continue
            email = ask("Email для сертификата HTTPS")
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                print("Проверь email.")
                continue
            if not all(port_available(port) for port in (80, 443)):
                print("80/443 уже заняты. Выбери режим 3 для существующего прокси.")
                continue
            print("DNS A/AAAA должен указывать на этот сервер; входящие TCP 80/443 должны быть доступны.")
            return {"DND_NETWORK_MODE": "https", "DND_TRUST_PROXY": "1", "PUBLIC_URL": "https://" + domain, "DND_DOMAIN": domain, "ACME_EMAIL": email, "HTTP_PORT": "8080", "BIND_IP": "127.0.0.1"}
        if mode not in ("2", "3"):
            continue
        port = ask("Свободный порт приложения", "8080")
        bind = "127.0.0.1" if mode == "3" else "0.0.0.0"
        if not port.isdigit() or not 1024 <= int(port) <= 65535 or not port_available(int(port), bind):
            print("Нужен свободный порт от 1024 до 65535.")
            continue
        if mode == "3":
            url = ask("Полный внешний URL сайта, например https://game.example.org")
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
                print("Нужен URL без учётных данных, пути и параметров.")
                continue
            url = url.rstrip("/")
        else:
            address = ask("IP сервера, доступный игрокам")
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                print("Укажи IPv4 или IPv6 адрес.")
                continue
            if ip.version == 6:
                print("Для IPv6 выбери режим 3 с IPv6 обратным прокси; прямой режим пока IPv4.")
                continue
            url = f"http://{address}:{port}"
        return {"DND_NETWORK_MODE": "external" if mode == "3" else "http", "DND_TRUST_PROXY": "1" if mode == "3" else "0", "PUBLIC_URL": url, "HTTP_PORT": port, "BIND_IP": bind, "DND_DOMAIN": "", "ACME_EMAIL": ""}
def command(args, proxy=""):
    env = dict(os.environ)
    for key in ("DOCKER_CONTEXT", "DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
        env.pop(key, None)
    if args and args[0] == "docker":
        args = ["docker", "--host", "unix:///var/run/docker.sock", *args[1:]]
    if proxy:
        env.update(https_proxy=proxy, http_proxy=proxy, HTTPS_PROXY=proxy, HTTP_PROXY=proxy)
    subprocess.run(args, env=env, check=True)
def ensure_docker(proxy):
    if shutil.which("docker"):
        command(["docker", "info", "--format", "Docker daemon ready"])
        command(["docker", "compose", "version"])
        return
    existing_daemon = subprocess.run(["systemctl", "is-active", "--quiet", "docker"], capture_output=True).returncode == 0
    docker_data = pathlib.Path("/var/lib/docker")
    existing_data = docker_data.is_dir() and any(docker_data.iterdir())
    print("Устанавливаю Docker Engine и Compose из официального apt-репозитория Docker.")
    command(["apt-get", "update"], proxy)
    command(["apt-get", "install", "-y", "ca-certificates", "curl"], proxy)
    pathlib.Path("/etc/apt/keyrings").mkdir(mode=0o755, exist_ok=True)
    command(["curl", "--fail", "--silent", "--show-error", "--location", "--proto", "=https", "https://download.docker.com/linux/ubuntu/gpg", "-o", "/etc/apt/keyrings/docker.asc"], proxy)
    os.chmod("/etc/apt/keyrings/docker.asc", 0o644)
    atomic("/etc/apt/sources.list.d/dnd-ai-arahort-docker.sources", "Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: noble\nComponents: stable\nArchitectures: amd64\nSigned-By: /etc/apt/keyrings/docker.asc\n", 0o644)
    command(["apt-get", "update"], proxy)
    command(["apt-get", "install", "-y", "docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin", "docker-compose-plugin"], proxy)
    if proxy and not existing_daemon and not existing_data:
        print("Новый Docker: сохраняю отдельный защищённый proxy drop-in для скачивания образов.")
        escaped = proxy.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
        dropin = pathlib.Path("/etc/systemd/system/docker.service.d/dnd-ai-arahort-proxy.conf")
        if dropin.exists():
            raise ValueError("Docker proxy drop-in already exists; inspect it before continuing")
        atomic(dropin, '[Service]\nEnvironment="HTTP_PROXY=' + escaped + '"\nEnvironment="HTTPS_PROXY=' + escaped + '"\nEnvironment="NO_PROXY=localhost,127.0.0.1,::1"\n')
        command(["systemctl", "daemon-reload"])
        # Docker was installed by this invocation: no pre-existing daemon is restarted.
        command(["systemctl", "restart", "docker"])
    elif proxy:
        print("Обнаружен существующий Docker daemon/каталог данных: его proxy-конфигурация сохранена без изменений.")
    command(["systemctl", "enable", "--now", "docker"])
def copy_distribution(target, local):
    if target == SOURCE:
        return
    target.mkdir(mode=0o755, parents=True, exist_ok=True)
    for name in ("install.sh", "Dockerfile", "compose.yaml", "compose.http.yaml", "compose.https.yaml", "compose.local.yaml", ".dockerignore", ".env.example", "release-source.json", "README.md", "LICENSE"):
        if (SOURCE / name).is_file():
            shutil.copyfile(SOURCE / name, target / name)
    for name in ("ops", "docs"):
        if (SOURCE / name).is_dir():
            shutil.copytree(SOURCE / name, target / name, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if local:
        shutil.copytree(SOURCE / "app", target / "app", dirs_exist_ok=True)
def setup_service(host):
    unit = f"[Unit]\nDescription=DND AI Arahort restricted updater\nAfter=docker.service\nRequires=docker.service\n[Service]\nType=simple\nUser=root\nGroup=root\nWorkingDirectory={host.root}\nExecStart=/usr/bin/python3 {host.root}/ops/host.py serve\nRestart=on-failure\nRestartSec=3\nUMask=0077\nNoNewPrivileges=true\nPrivateTmp=true\nProtectSystem=strict\nProtectHome=true\nReadWritePaths={host.root} {host.state} /run/dnd-ai-arahort\nRestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n[Install]\nWantedBy=multi-user.target\n"
    atomic("/etc/systemd/system/dnd-ai-arahort-updater.service", unit, 0o644)
    atomic("/usr/local/bin/dndctl", f'#!/bin/sh\nexec /usr/bin/python3 "{host.root}/ops/host.py" "$@"\n', 0o755)
    atomic("/etc/tmpfiles.d/dnd-ai-arahort.conf", "d /run/dnd-ai-arahort 0750 root dnd-updater -\n", 0o644)
    command(["systemctl", "daemon-reload"])
    command(["systemctl", "enable", "--now", "dnd-ai-arahort-updater.service"])
def initialize_agy(home):
    home = pathlib.Path(home).resolve()
    directory = home
    for name in (".gemini", "antigravity-cli"):
        directory = directory / name
        if directory.is_symlink():
            raise ValueError("AGY settings directory cannot be a symlink")
        if not directory.exists():
            directory.mkdir(mode=0o700)
            os.chown(directory, 1000, 1000)
    path = directory / "settings.json"
    if path.is_symlink():
        raise ValueError("AGY settings cannot be a symlink")
    settings = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(settings, dict):
        raise ValueError("Invalid existing AGY settings; fix JSON before resuming")
    settings.setdefault("toolPermission", "request-review")
    settings.setdefault("allowNonWorkspaceAccess", False)
    permissions = settings.setdefault("permissions", {})
    if not isinstance(permissions, dict) or not isinstance(permissions.setdefault("deny", []), list):
        raise ValueError("Invalid existing AGY permission settings")
    for rule in ("command(*)", "mcp(*)", "read_url(*)"):
        if rule not in permissions["deny"]:
            permissions["deny"].append(rule)
    atomic(path, json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
    os.chown(path, 1000, 1000)
def build_proxy_args(proxy):
    environment = {key: proxy for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")} if proxy else {}
    arguments = [value for key in environment for value in ("--build-arg", key)]
    return arguments, environment
def finish(host, local):
    host.initialize_control()
    initialize_agy(host.state / "home")
    if local:
        build_args, build_env = build_proxy_args(host.env.get("AGY_PROXY", ""))
        release = json.loads((host.root / "app/release.json").read_text())
        version = release.get("version", "")
        if not isinstance(version, str) or not VERSION.fullmatch(version) or release.get("schemaVersion") != 1:
            raise ValueError("Invalid local release version/schema")
        host.run(["docker", "build", *build_args, "--label", "org.opencontainers.image.version=" + version, "--tag", "dnd-ai-arahort:local", str(host.root)], extra_env=build_env, timeout=1800, output=sys.stdout.buffer)
        immutable = host.run(["docker", "image", "inspect", "dnd-ai-arahort:local", "--format", "{{.Id}}"], timeout=30).stdout.decode().strip()
        set_env(host.env_path, {"DND_IMAGE": immutable, "DND_VERSION": version})
        host.reload()
    else:
        if "@sha256:" not in host.env.get("DND_IMAGE", ""):
            host.check()
            manifest = json.loads(host.release_path.read_text())
            set_env(host.env_path, {"DND_IMAGE": manifest["image"], "DND_VERSION": manifest["version"]})
            host.reload()
        host.run(["docker", "pull", host.env["DND_IMAGE"]], timeout=900, output=sys.stdout.buffer)
    host.compose("up", "-d", "--wait", "db", timeout=180, output=sys.stdout.buffer)
    if not (host.state / "bootstrap.done").exists():
        checked = host.compose("run", "--rm", "-T", "--no-deps", "app", "node", "server/admin-cli.mjs", "bootstrap-status", timeout=180)
        initialized = json.loads(checked.stdout).get("initialized")
        if initialized is not True:
            name, password = admin_question()
            host.compose("run", "--rm", "-T", "--no-deps", "app", "node", "server/admin-cli.mjs", "bootstrap", "--name", name, input=(password + "\n").encode(), timeout=180)
            del password
        atomic(host.state / "bootstrap.done", "Administrator initialized.\n")
    setup_service(host)
    host.compose("up", "-d", "--wait", timeout=240, output=sys.stdout.buffer)
    host.wait_healthy()
    print("\nПриложение отвечает: " + host.env["PUBLIC_URL"])
    print("Теперь активируй Gemini своим Google-аккаунтом: sudo --preserve-env=SSH_CONNECTION,SSH_CLIENT,SSH_TTY dndctl login")
    print("Открой показанную AGY ссылку на своём компьютере и заверши вход по подсказкам CLI.")
    print("После входа отправь короткое приветствие; доступность генерации проверяется реальным ответом.")
    print("При доступе через внешний прокси настрой WebSocket на 127.0.0.1:" + host.env.get("HTTP_PORT", "8080"))
def main():
    parser = argparse.ArgumentParser(description="Установка DND AI Arahort на Ubuntu 24.04 x86_64")
    parser.add_argument("--local", action="store_true", help="Собрать образ из готового app/, без исходников TypeScript")
    parser.add_argument("--resume", action="store_true", help="Продолжить незавершённую установку, сохранив .env и данные")
    parser.add_argument("--directory", default="/opt/dnd-ai-arahort")
    args = parser.parse_args()
    if os.geteuid() != 0 or platform.machine() != "x86_64":
        parser.error("Нужны sudo/root и x86_64")
    release = pathlib.Path("/etc/os-release").read_text()
    if not re.search(r'^ID=ubuntu$', release, re.M) or not re.search(r'^VERSION_ID="24\.04"$', release, re.M):
        parser.error("Первый выпуск поддерживает Ubuntu 24.04 x86_64")
    target = pathlib.Path(args.directory).resolve()
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(target)) or target == pathlib.Path("/"):
        parser.error("Недопустимая папка установки")
    if (target / ".env").exists():
        if not args.resume:
            parser.error("Установка уже существует. Обновление: sudo dndctl update; продолжение установки: --resume")
        host = Host(target)
        local = args.local or host.env.get("DND_INSTALL_MODE") == "local"
        ensure_docker(host.env.get("AGY_PROXY", ""))
        return finish(host, local)
    repository = release_repository(SOURCE, args.local)
    if args.local and not (SOURCE / "app/server/index.mjs").is_file():
        parser.error("Нет готового app/server/index.mjs; сначала подготовь публичный пакет сборки")
    if target.exists() and target != SOURCE and any(target.iterdir()):
        parser.error("Папка уже содержит файлы; существующую установку не перезаписываю")
    proxy = proxy_question()
    network = network_question()
    ensure_docker(proxy)
    copy_distribution(target, args.local)
    try:
        gid = grp.getgrnam("dnd-updater").gr_gid
    except KeyError:
        command(["groupadd", "--system", "dnd-updater"])
        gid = grp.getgrnam("dnd-updater").gr_gid
    state = pathlib.Path("/var/lib/dnd-ai-arahort")
    if state.exists() and any(state.iterdir()):
        parser.error("Папка /var/lib/dnd-ai-arahort уже содержит данные. Проверь прежнюю установку вручную.")
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(state, 0o700)
    for name in ("data", "home", "postgres", "backups", "caddy-data", "caddy-config"):
        path = state / name
        path.mkdir(mode=0o700, exist_ok=True)
        if name in ("data", "home"):
            os.chown(path, 1000, 1000)
    runtime = pathlib.Path("/run/dnd-ai-arahort")
    runtime.mkdir(mode=0o750, exist_ok=True)
    os.chown(runtime, 0, gid)
    os.chmod(runtime, 0o750)
    values = {"COMPOSE_PROJECT_NAME": "dnd-ai-arahort", "DND_INSTALL_MODE": "local" if args.local else "remote", "DND_STATE_DIR": str(state), "DND_IMAGE": "dnd-ai-arahort:local", "DND_VERSION": "local", "DND_REPOSITORY": repository, "PG_PASSWORD": secrets.token_urlsafe(36), "DND_SESSION_SECRET": secrets.token_urlsafe(48), "DND_UPDATE_TOKEN": secrets.token_urlsafe(48), "DND_UPDATER_GID": str(gid), "AGY_PROXY": proxy, "DND_MODEL_EFFORT": "high", "DND_START_MAINTENANCE": "0", **network}
    set_env(target / ".env", values)
    host = Host(target)
    if not args.local:
        host.check()
        manifest = json.loads(host.release_path.read_text())
        set_env(host.env_path, {"DND_IMAGE": manifest["image"], "DND_VERSION": manifest["version"]})
        host.reload()
    finish(host, args.local)
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nУстановка прервана. Существующие данные сохранены. Для продолжения используй --resume.", file=sys.stderr)
        sys.exit(130)
    except Exception as error:
        # subprocess errors deliberately omit environment values and captured credentials.
        print("Установка не завершена: " + str(error), file=sys.stderr)
        print("Проверь ошибку и повтори с --resume; .env, PostgreSQL и OAuth-профиль сохраняются.", file=sys.stderr)
        sys.exit(1)
