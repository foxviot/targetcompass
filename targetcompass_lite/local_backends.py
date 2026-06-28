import hashlib
import hmac
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .evidence_db import SCHEMA, INDEX_SQL
from .v4 import content_hash, file_hash, v4_dir


LOCAL_BACKEND_SCHEMA = "v4.local_backend_stack/0.1"
LOCAL_BACKEND_CHECK_SCHEMA = "v4.local_backend_check/0.1"
LOCAL_BACKEND_SYNC_SCHEMA = "v4.local_backend_sync/0.1"
V5_ACTIVE_BACKENDS_SCHEMA = "v5.active_backends/0.1"

DEFAULT_POSTGRES_DSN = "postgresql://targetcompass:targetcompass@127.0.0.1:55432/targetcompass"
DEFAULT_MINIO_ENDPOINT = "http://127.0.0.1:59000"
DEFAULT_MINIO_BUCKET = "targetcompass-artifacts"
DEFAULT_MINIO_ACCESS_KEY = "targetcompass"
DEFAULT_MINIO_SECRET_KEY = "targetcompass-local-secret"
DEFAULT_POSTGRES_IMAGE = "docker.m.daocloud.io/library/postgres:16-alpine"
DEFAULT_MINIO_IMAGE = "docker.m.daocloud.io/minio/minio:latest"


def prepare_local_backend_stack(project_dir: Path) -> dict[str, Any]:
    infra = project_dir / "infra" / "local_backends"
    infra.mkdir(parents=True, exist_ok=True)
    compose_path = infra / "docker-compose.yml"
    env_path = infra / ".env.example"
    start_path = infra / "start_local_backends.ps1"
    stop_path = infra / "stop_local_backends.ps1"

    compose_path.write_text(_compose_yaml(), encoding="utf-8")
    env_path.write_text(_env_example(project_dir), encoding="utf-8")
    start_path.write_text(_start_script(), encoding="utf-8")
    stop_path.write_text(_stop_script(), encoding="utf-8")

    payload = {
        "schema_version": LOCAL_BACKEND_SCHEMA,
        "project_id": project_dir.name,
        "generated_at": _now(),
        "mode": "local_docker_compose",
        "compose_file": _rel(compose_path, project_dir),
        "env_example": _rel(env_path, project_dir),
        "start_script": _rel(start_path, project_dir),
        "stop_script": _rel(stop_path, project_dir),
        "services": {
            "postgres": {
                "image": DEFAULT_POSTGRES_IMAGE,
                "port": 55432,
                "database": "targetcompass",
                "user": "targetcompass",
                "dsn_env": "TARGETCOMPASS_POSTGRES_DSN",
            },
            "minio": {
                "image": DEFAULT_MINIO_IMAGE,
                "api_port": 59000,
                "console_port": 59001,
                "endpoint_env": "TARGETCOMPASS_MINIO_ENDPOINT",
                "bucket_env": "TARGETCOMPASS_OBJECT_BUCKET",
            },
        },
        "commands": {
            "start": f"powershell -ExecutionPolicy Bypass -File { _rel(start_path, project_dir) }",
            "check": f"python tc_lite.py local-backends-check --project {project_dir.name}",
            "sync": f"python tc_lite.py local-backends-sync --project {project_dir.name}",
        },
    }
    payload["backend_stack_hash"] = content_hash(payload)
    out = v4_dir(project_dir) / "local_backend_stack.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def check_local_backends(project_dir: Path, migrate: bool = True, bucket: str = "") -> dict[str, Any]:
    env = local_backend_env(project_dir, bucket=bucket)
    postgres = _check_postgres(env, migrate=migrate)
    minio = _check_minio(env, ensure_bucket=True)
    checks = [
        _check("docker_available", _docker_available(), "Docker CLI is available.", "Start Docker Desktop and rerun local backend check."),
        _check("postgres_live", postgres["status"] == "PASS", "PostgreSQL accepted a live query.", postgres.get("failure_reason", "PostgreSQL is not reachable.")),
        _check("postgres_schema_ready", bool(postgres.get("schema_ready")), "PostgreSQL Evidence schema is migrated.", postgres.get("migration_error", "Run with --migrate after PostgreSQL starts.")),
        _check("minio_live", minio["status"] == "PASS", "MinIO/S3 endpoint is reachable.", minio.get("failure_reason", "MinIO endpoint is not reachable.")),
        _check("minio_bucket_ready", bool(minio.get("bucket_ready")), "Object bucket exists and is writable.", minio.get("bucket_error", "Create bucket or check MinIO credentials.")),
    ]
    status = "READY" if all(row["status"] == "PASS" for row in checks) else "BLOCKED"
    payload = {
        "schema_version": LOCAL_BACKEND_CHECK_SCHEMA,
        "project_id": project_dir.name,
        "generated_at": _now(),
        "status": status,
        "active_backends": {
            "evidence_db": "postgres_local" if postgres["status"] == "PASS" and postgres.get("schema_ready") else "sqlite_local",
            "object_store": "minio_local" if minio["status"] == "PASS" and minio.get("bucket_ready") else "local_filesystem",
        },
        "postgres": postgres,
        "minio": minio,
        "checks": checks,
        "env": {
            "TARGETCOMPASS_POSTGRES_DSN": env["TARGETCOMPASS_POSTGRES_DSN"],
            "TARGETCOMPASS_MINIO_ENDPOINT": env["TARGETCOMPASS_MINIO_ENDPOINT"],
            "TARGETCOMPASS_OBJECT_BUCKET": env["TARGETCOMPASS_OBJECT_BUCKET"],
            "TARGETCOMPASS_OBJECT_PREFIX": env["TARGETCOMPASS_OBJECT_PREFIX"],
        },
    }
    payload["backend_check_hash"] = content_hash(payload)
    out = v4_dir(project_dir) / "local_backend_check.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def sync_local_backends(project_dir: Path, bucket: str = "") -> dict[str, Any]:
    env = local_backend_env(project_dir, bucket=bucket)
    check = check_local_backends(project_dir, migrate=True, bucket=bucket)
    evidence = _sync_evidence_to_postgres(project_dir, env) if check["postgres"].get("schema_ready") else {"status": "SKIPPED", "failure_reason": "PostgreSQL schema is not ready."}
    objects = _sync_artifacts_to_minio(project_dir, env) if check["minio"].get("bucket_ready") else {"status": "SKIPPED", "failure_reason": "MinIO bucket is not ready.", "objects": []}
    status = "READY" if evidence.get("status") == "PASS" and objects.get("status") == "PASS" else "BLOCKED"
    payload = {
        "schema_version": LOCAL_BACKEND_SYNC_SCHEMA,
        "project_id": project_dir.name,
        "generated_at": _now(),
        "status": status,
        "backend_check_ref": "v4/local_backend_check.json",
        "evidence_postgres_sync": evidence,
        "object_store_sync": objects,
    }
    payload["backend_sync_hash"] = content_hash(payload)
    out = v4_dir(project_dir) / "local_backend_sync.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def activate_v5_local_backends(project_dir: Path, bucket: str = "") -> dict[str, Any]:
    check = check_local_backends(project_dir, migrate=True, bucket=bucket)
    sync = sync_local_backends(project_dir, bucket=bucket) if check.get("status") == "READY" else {"status": "BLOCKED"}
    active = check.get("status") == "READY" and sync.get("status") == "READY"
    env = local_backend_env(project_dir, bucket=bucket)
    payload = {
        "schema_version": V5_ACTIVE_BACKENDS_SCHEMA,
        "project_id": project_dir.name,
        "generated_at": _now(),
        "status": "ACTIVE" if active else "FALLBACK",
        "active_backends": {
            "evidence_db": "postgres_local" if active else "sqlite_local",
            "object_store": "minio_local" if active else "local_filesystem",
        },
        "backend_check_ref": "v4/local_backend_check.json",
        "backend_sync_ref": "v4/local_backend_sync.json" if sync.get("status") == "READY" else "",
        "env": {
            "TARGETCOMPASS_POSTGRES_DSN": env["TARGETCOMPASS_POSTGRES_DSN"],
            "TARGETCOMPASS_MINIO_ENDPOINT": env["TARGETCOMPASS_MINIO_ENDPOINT"],
            "TARGETCOMPASS_OBJECT_BUCKET": env["TARGETCOMPASS_OBJECT_BUCKET"],
            "TARGETCOMPASS_OBJECT_PREFIX": env["TARGETCOMPASS_OBJECT_PREFIX"],
        },
        "fallback_reason": "" if active else _backend_fallback_reason(check, sync),
        "policy": {
            "read_preference": "postgres_local_then_sqlite" if active else "sqlite_local",
            "artifact_write_preference": "minio_local_then_filesystem" if active else "local_filesystem",
            "do_not_delete_local_files": True,
        },
    }
    payload["active_backend_hash"] = content_hash(payload)
    out = project_dir / "v5" / "active_backends.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def local_backend_env(project_dir: Path, bucket: str = "") -> dict[str, str]:
    return {
        "TARGETCOMPASS_POSTGRES_DSN": os.environ.get("TARGETCOMPASS_POSTGRES_DSN", DEFAULT_POSTGRES_DSN),
        "TARGETCOMPASS_MINIO_ENDPOINT": os.environ.get("TARGETCOMPASS_MINIO_ENDPOINT") or os.environ.get("TARGETCOMPASS_S3_ENDPOINT", DEFAULT_MINIO_ENDPOINT),
        "TARGETCOMPASS_OBJECT_BUCKET": bucket or os.environ.get("TARGETCOMPASS_OBJECT_BUCKET", DEFAULT_MINIO_BUCKET),
        "TARGETCOMPASS_OBJECT_PREFIX": os.environ.get("TARGETCOMPASS_OBJECT_PREFIX", f"targetcompass/{project_dir.name}/"),
        "TARGETCOMPASS_S3_ACCESS_KEY": os.environ.get("TARGETCOMPASS_S3_ACCESS_KEY", DEFAULT_MINIO_ACCESS_KEY),
        "TARGETCOMPASS_S3_SECRET_KEY": os.environ.get("TARGETCOMPASS_S3_SECRET_KEY", DEFAULT_MINIO_SECRET_KEY),
        "TARGETCOMPASS_S3_REGION": os.environ.get("TARGETCOMPASS_S3_REGION", "us-east-1"),
    }


def _backend_fallback_reason(check: dict[str, Any], sync: dict[str, Any]) -> str:
    if check.get("status") != "READY":
        failed = [row.get("check_id", "") for row in check.get("checks", []) if row.get("status") != "PASS"]
        return "backend check not ready: " + ", ".join(failed or ["unknown"])
    if sync.get("status") != "READY":
        return "backend sync not ready"
    return "local backend activation blocked"


def _check_postgres(env: dict[str, str], migrate: bool) -> dict[str, Any]:
    if not _docker_available():
        return {"status": "FAIL", "failure_reason": "Docker CLI is not available.", "schema_ready": False}
    dsn = env["TARGETCOMPASS_POSTGRES_DSN"]
    container_dsn = _container_dsn(dsn)
    probe = _docker_run([_postgres_client_image(), "psql", container_dsn, "-tAc", "SELECT 1"])
    if probe.returncode != 0 or "1" not in probe.stdout:
        return {"status": "FAIL", "failure_reason": (probe.stderr or probe.stdout or "psql probe failed").strip(), "schema_ready": False}
    migration_error = ""
    schema_ready = False
    if migrate:
        sql = _postgres_schema_sql()
        migration = _docker_run([_postgres_client_image(), "psql", container_dsn, "-v", "ON_ERROR_STOP=1", "-c", sql], timeout=90)
        if migration.returncode == 0:
            schema_ready = True
        else:
            migration_error = (migration.stderr or migration.stdout or "PostgreSQL migration failed").strip()
    return {
        "status": "PASS",
        "dsn_env": "TARGETCOMPASS_POSTGRES_DSN",
        "dsn": _redact_dsn(dsn),
        "schema_ready": schema_ready,
        "migration_error": migration_error,
        "probe_stdout": probe.stdout.strip(),
    }


def _check_minio(env: dict[str, str], ensure_bucket: bool) -> dict[str, Any]:
    endpoint = env["TARGETCOMPASS_MINIO_ENDPOINT"].rstrip("/")
    bucket = env["TARGETCOMPASS_OBJECT_BUCKET"]
    access_key = env["TARGETCOMPASS_S3_ACCESS_KEY"]
    secret_key = env["TARGETCOMPASS_S3_SECRET_KEY"]
    try:
        status = _s3_request("GET", endpoint, "", access_key, secret_key, region=env["TARGETCOMPASS_S3_REGION"])
    except Exception as exc:
        return {"status": "FAIL", "failure_reason": str(exc), "bucket_ready": False}
    bucket_error = ""
    bucket_ready = False
    if ensure_bucket:
        try:
            _s3_request("PUT", endpoint, bucket, access_key, secret_key, region=env["TARGETCOMPASS_S3_REGION"])
            probe_key = env["TARGETCOMPASS_OBJECT_PREFIX"].rstrip("/") + "/_backend_probe.txt"
            _s3_request("PUT", endpoint, f"{bucket}/{probe_key}", access_key, secret_key, body=b"targetcompass-local-backend")
            bucket_ready = True
        except Exception as exc:
            bucket_error = str(exc)
    return {
        "status": "PASS",
        "endpoint": endpoint,
        "bucket": bucket,
        "bucket_ready": bucket_ready,
        "bucket_error": bucket_error,
        "probe_status": status,
    }


def _sync_evidence_to_postgres(project_dir: Path, env: dict[str, str]) -> dict[str, Any]:
    sqlite_path = project_dir / "evidence.sqlite"
    if not sqlite_path.exists():
        return {"status": "SKIPPED", "failure_reason": "evidence.sqlite does not exist."}
    csv_path = v4_dir(project_dir) / "postgres_evidence_export.tsv"
    _export_sqlite_evidence(sqlite_path, csv_path)
    container_path = f"/import/{csv_path.name}"
    sql_path = v4_dir(project_dir) / "postgres_evidence_import.sql"
    sql_path.write_text(
        "\n".join(
            [
                "TRUNCATE evidence_item;",
                f"\\copy evidence_item FROM '{container_path}' WITH (FORMAT csv, HEADER true, DELIMITER E'\\t', NULL '');",
                "INSERT INTO evidence_metadata(key,value) VALUES ('last_sqlite_sync_at', now()::text) ON CONFLICT (key) DO UPDATE SET value=excluded.value;",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            _docker_bin() or "docker",
            "run",
            "--rm",
            "-v",
            f"{str(csv_path.parent.resolve())}:/import:ro",
            _postgres_client_image(),
            "psql",
            _container_dsn(env["TARGETCOMPASS_POSTGRES_DSN"]),
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            "/import/postgres_evidence_import.sql",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    count = _postgres_count(env["TARGETCOMPASS_POSTGRES_DSN"])
    return {
        "status": "PASS" if result.returncode == 0 else "FAIL",
        "sqlite_export": _rel(csv_path, project_dir),
        "import_sql": _rel(sql_path, project_dir),
        "source_hash": file_hash(sqlite_path),
        "export_hash": file_hash(csv_path),
        "postgres_row_count": count,
        "failure_reason": "" if result.returncode == 0 else (result.stderr or result.stdout),
        "container_path": container_path,
    }


def _sync_artifacts_to_minio(project_dir: Path, env: dict[str, str]) -> dict[str, Any]:
    objects = []
    for path in _artifact_candidates(project_dir):
        rel = _rel(path, project_dir)
        key = env["TARGETCOMPASS_OBJECT_PREFIX"].rstrip("/") + "/" + rel
        data = path.read_bytes()
        _s3_request(
            "PUT",
            env["TARGETCOMPASS_MINIO_ENDPOINT"].rstrip("/"),
            f"{env['TARGETCOMPASS_OBJECT_BUCKET']}/{key}",
            env["TARGETCOMPASS_S3_ACCESS_KEY"],
            env["TARGETCOMPASS_S3_SECRET_KEY"],
            body=data,
            region=env["TARGETCOMPASS_S3_REGION"],
        )
        objects.append({"path": rel, "uri": f"s3://{env['TARGETCOMPASS_OBJECT_BUCKET']}/{key}", "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    manifest_path = v4_dir(project_dir) / "minio_artifact_manifest.json"
    manifest = {
        "schema_version": "v4.minio_artifact_manifest/0.1",
        "project_id": project_dir.name,
        "generated_at": _now(),
        "bucket": env["TARGETCOMPASS_OBJECT_BUCKET"],
        "prefix": env["TARGETCOMPASS_OBJECT_PREFIX"],
        "object_count": len(objects),
        "objects": objects,
    }
    manifest["manifest_hash"] = content_hash(manifest)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"status": "PASS", "manifest": _rel(manifest_path, project_dir), "object_count": len(objects), "objects": objects}


def _artifact_candidates(project_dir: Path) -> list[Path]:
    roots = [project_dir / "reports", project_dir / "results", project_dir / "v4"]
    suffixes = {".html", ".json", ".tsv", ".csv", ".txt", ".md", ".docx", ".zip"}
    paths: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in suffixes and path.stat().st_size <= 25 * 1024 * 1024:
                paths.append(path)
    return sorted(paths)


def _export_sqlite_evidence(sqlite_path: Path, csv_path: Path) -> None:
    import csv
    import sqlite3

    columns = [
        "evidence_id",
        "project_id",
        "entity_symbol",
        "entity_type",
        "disease_context",
        "organism",
        "tissue",
        "route",
        "evidence_type",
        "direction",
        "effect_size",
        "p_value",
        "quality_score",
        "evidence_level",
        "evidence_weight",
        "evidence_basis",
        "review_status",
        "source_dataset",
        "artifact_path",
        "run_id",
        "artifact_id",
        "module_version",
        "limitation",
        "created_at",
    ]
    con = sqlite3.connect(sqlite_path, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(f"SELECT {', '.join(columns)} FROM evidence_item ORDER BY evidence_id").fetchall()
    finally:
        con.close()
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row[column] if row[column] is not None else "" for column in columns})


def _postgres_count(dsn: str) -> int:
    result = _docker_run([_postgres_client_image(), "psql", _container_dsn(dsn), "-tAc", "SELECT COUNT(*) FROM evidence_item"], timeout=60)
    try:
        return int(result.stdout.strip())
    except ValueError:
        return -1


def _postgres_schema_sql() -> str:
    sql = SCHEMA.replace("REAL", "DOUBLE PRECISION").replace("TEXT PRIMARY KEY", "TEXT PRIMARY KEY")
    indexes = ";\n".join(INDEX_SQL) + ";"
    return sql + "\n" + indexes


def _s3_request(method: str, endpoint: str, path: str, access_key: str, secret_key: str, body: bytes = b"", region: str = "us-east-1") -> int:
    parsed_path = "/" + "/".join(quote(part) for part in path.split("/") if part)
    if not parsed_path:
        parsed_path = "/"
    url = endpoint.rstrip("/") + parsed_path
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()
    host = endpoint.split("://", 1)[-1]
    canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join([method, parsed_path, "", canonical_headers, signed_headers, payload_hash])
    scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()])
    signature = hmac.new(_signing_key(secret_key, date_stamp, region), string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    auth = f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"
    request = Request(url, data=body if method in {"PUT", "POST"} else None, method=method)
    request.add_header("Host", host)
    request.add_header("X-Amz-Date", amz_date)
    request.add_header("X-Amz-Content-Sha256", payload_hash)
    request.add_header("Authorization", auth)
    try:
        with urlopen(request, timeout=15) as response:
            return response.status
    except HTTPError as exc:
        if method == "PUT" and exc.code in {200, 409}:
            return exc.code
        raise RuntimeError(f"S3 {method} {path or '/'} failed: HTTP {exc.code} {exc.read().decode('utf-8', 'replace')[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"S3 {method} {path or '/'} failed: {exc}") from exc


def _signing_key(secret_key: str, date_stamp: str, region: str) -> bytes:
    k_date = hmac.new(("AWS4" + secret_key).encode("utf-8"), date_stamp.encode("utf-8"), hashlib.sha256).digest()
    k_region = hmac.new(k_date, region.encode("utf-8"), hashlib.sha256).digest()
    k_service = hmac.new(k_region, b"s3", hashlib.sha256).digest()
    return hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()


def _docker_available() -> bool:
    return _docker_bin() is not None


def _docker_run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    docker = _docker_bin()
    if not docker:
        raise FileNotFoundError("Docker CLI is not available.")
    return subprocess.run([docker, "run", "--rm", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def _docker_bin() -> str | None:
    found = shutil.which("docker")
    if found:
        return found
    for path in [
        Path("C:/Program Files/Docker/Docker/resources/bin/docker.exe"),
        Path("C:/Program Files/Docker/Docker/resources/bin/docker"),
    ]:
        if path.exists():
            return str(path)
    return None


def _postgres_client_image() -> str:
    return os.environ.get("TARGETCOMPASS_POSTGRES_IMAGE", DEFAULT_POSTGRES_IMAGE)


def _check(check_id: str, passed: bool, ok: str, remediation: str) -> dict[str, Any]:
    return {"check_id": check_id, "status": "PASS" if passed else "FAIL", "message": ok if passed else remediation, "remediation": "" if passed else remediation}


def _redact_dsn(dsn: str) -> str:
    if "@" not in dsn or "://" not in dsn:
        return dsn
    scheme, rest = dsn.split("://", 1)
    return scheme + "://***@" + rest.split("@", 1)[1]


def _container_dsn(dsn: str) -> str:
    return dsn.replace("@127.0.0.1:", "@host.docker.internal:").replace("@localhost:", "@host.docker.internal:")


def _rel(path: Path, project_dir: Path) -> str:
    try:
        return str(path.relative_to(project_dir)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compose_yaml() -> str:
    return f"""services:
  postgres:
    image: ${{TARGETCOMPASS_POSTGRES_IMAGE:-{DEFAULT_POSTGRES_IMAGE}}}
    container_name: targetcompass-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: targetcompass
      POSTGRES_USER: targetcompass
      POSTGRES_PASSWORD: targetcompass
    ports:
      - "55432:5432"
    volumes:
      - targetcompass_pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U targetcompass -d targetcompass"]
      interval: 10s
      timeout: 5s
      retries: 10

  minio:
    image: ${{TARGETCOMPASS_MINIO_IMAGE:-{DEFAULT_MINIO_IMAGE}}}
    container_name: targetcompass-minio
    restart: unless-stopped
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: {DEFAULT_MINIO_ACCESS_KEY}
      MINIO_ROOT_PASSWORD: {DEFAULT_MINIO_SECRET_KEY}
    ports:
      - "59000:9000"
      - "59001:9001"
    volumes:
      - targetcompass_minio:/data
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O- http://127.0.0.1:9000/minio/health/live || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 10

volumes:
  targetcompass_pgdata:
  targetcompass_minio:
"""


def _env_example(project_dir: Path) -> str:
    return f"""TARGETCOMPASS_POSTGRES_DSN={DEFAULT_POSTGRES_DSN}
TARGETCOMPASS_POSTGRES_IMAGE={DEFAULT_POSTGRES_IMAGE}
TARGETCOMPASS_MINIO_IMAGE={DEFAULT_MINIO_IMAGE}
TARGETCOMPASS_MINIO_ENDPOINT={DEFAULT_MINIO_ENDPOINT}
TARGETCOMPASS_OBJECT_BUCKET={DEFAULT_MINIO_BUCKET}
TARGETCOMPASS_OBJECT_PREFIX=targetcompass/{project_dir.name}/
TARGETCOMPASS_S3_ACCESS_KEY={DEFAULT_MINIO_ACCESS_KEY}
TARGETCOMPASS_S3_SECRET_KEY={DEFAULT_MINIO_SECRET_KEY}
TARGETCOMPASS_S3_REGION=us-east-1
"""


def _start_script() -> str:
    return """$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$DockerBin = "C:\\Program Files\\Docker\\Docker\\resources\\bin"
if (Test-Path $DockerBin) { $env:Path = "$DockerBin;$env:Path" }
docker compose -f (Join-Path $Here "docker-compose.yml") up -d
Write-Host "TargetCompass local PostgreSQL: 127.0.0.1:55432"
Write-Host "TargetCompass local MinIO API: http://127.0.0.1:59000"
Write-Host "TargetCompass local MinIO console: http://127.0.0.1:59001"
"""


def _stop_script() -> str:
    return """$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$DockerBin = "C:\\Program Files\\Docker\\Docker\\resources\\bin"
if (Test-Path $DockerBin) { $env:Path = "$DockerBin;$env:Path" }
docker compose -f (Join-Path $Here "docker-compose.yml") down
"""
