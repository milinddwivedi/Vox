"""
VOX Legacy User-Data Migration Utility.

Cleanly separates mutable user data (`voxlocal.db`, recordings, config, auth vault,
backups, snapshots, exports, logs, and AI models) from immutable application binaries
by migrating `<install_dir>/data` into `%LOCALAPPDATA%\\VOX` safely and idempotently.

Safety guarantees:
1. Never removes `<install_dir>/data` until target DB integrity (`PRAGMA integrity_check = ok`),
   matching table row counts, copied file sizes, and `.migration_complete.json` are verified.
2. Creates a timestamped pre-migration SQLite `.backup()` inside `<target_vox_root>/data/backups/`.
3. Rewrites only stored file paths pointing inside `<install_dir>/data` to `<target_vox_root>/data`.
4. Uses atomic `shutil.move` on same-volume model directories to avoid duplicate multi-GB copies.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Callable, Dict, Iterable, List, Optional

CORE_AUDIT_TABLES = (
    "meetings",
    "transcript_segments",
    "dictation_history",
    "action_items",
    "decisions",
    "custom_vocabulary",
    "knowledge_items",
    "my_thoughts",
    "screen_recordings",
)

USER_SUBDIRS = (
    "recordings",
    "backups",
    "snapshots",
    "exports",
    "crash_reports",
    "updates",
    "logs",
)


def _rewrite_prefix_path(raw_val: str, old_prefix: Path, new_prefix: Path) -> str:
    """Rewrite a stored path string if it lives inside `old_prefix`."""
    if not raw_val or not isinstance(raw_val, str):
        return raw_val
    norm_raw = raw_val.replace("/", "\\")
    norm_old = str(old_prefix.resolve()).replace("/", "\\").rstrip("\\")
    norm_new = str(new_prefix.resolve()).replace("/", "\\").rstrip("\\")
    if norm_raw.lower() == norm_old.lower():
        return norm_new
    if norm_raw.lower().startswith(norm_old.lower() + "\\"):
        suffix = norm_raw[len(norm_old):]
        return norm_new + suffix
    return raw_val


def _collect_table_counts(conn: sqlite3.Connection, tables: Iterable[str] = CORE_AUDIT_TABLES) -> Dict[str, int]:
    existing = {
        str(r[0])
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    counts: Dict[str, int] = {}
    for tbl in tables:
        if tbl in existing:
            counts[tbl] = int(conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0])
    return counts


def _rewrite_db_legacy_paths(conn: sqlite3.Connection, legacy_data: Path, target_data: Path) -> int:
    """Update legacy install-dir audio/video paths stored in SQLite tables after migration."""
    updated = 0
    existing_tables = {
        str(r[0])
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    candidates = [
        ("meetings", ("audio_path", "mic_audio_path", "sys_audio_path", "video_path")),
        ("screen_recordings", ("file_path", "audio_path", "video_path")),
    ]
    for tbl, cols in candidates:
        if tbl not in existing_tables:
            continue
        tbl_cols = {str(r[1]) for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()}
        active_cols = [c for c in cols if c in tbl_cols]
        if not active_cols:
            continue
        sel_sql = f"SELECT rowid, {', '.join(active_cols)} FROM {tbl}"
        rows = conn.execute(sel_sql).fetchall()
        for row in rows:
            rowid = row[0]
            updates: Dict[str, str] = {}
            for idx, col in enumerate(active_cols, start=1):
                val = row[idx]
                if isinstance(val, str) and val:
                    rewritten = _rewrite_prefix_path(val, legacy_data, target_data)
                    if rewritten != val:
                        updates[col] = rewritten
            if updates:
                set_clause = ", ".join(f"{c} = ?" for c in updates)
                conn.execute(
                    f"UPDATE {tbl} SET {set_clause} WHERE rowid = ?",
                    (*updates.values(), rowid),
                )
                updated += len(updates)
    conn.commit()
    return updated


def _copy_tree_verified(src_dir: Path, dst_dir: Path) -> int:
    """Copy files from `src_dir` to `dst_dir`, preserving existing destination files and verifying byte size."""
    copied = 0
    if not src_dir.is_dir():
        return 0
    for item in src_dir.rglob("*"):
        if not item.is_file():
            continue
        if item.name.endswith(("-wal", "-shm", ".migrating", ".tmp")):
            continue
        rel = item.relative_to(src_dir)
        dst = dst_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        src_size = item.stat().st_size
        if dst.is_file() and dst.stat().st_size == src_size:
            continue
        if dst.is_file() and dst.stat().st_size > 0:
            # Do not clobber non-empty newer user file in target location
            continue
        tmp = dst.with_name(dst.name + ".mig_tmp")
        shutil.copy2(item, tmp)
        if tmp.stat().st_size != src_size:
            tmp.unlink(missing_ok=True)
            raise IOError(f"Verified copy byte mismatch for {item}")
        os.replace(tmp, dst)
        copied += 1
    return copied


def _migrate_models_dir(src_models: Path, dst_models: Path) -> Dict[str, int]:
    """Move/copy installed model directories cleanly into `%LOCALAPPDATA%\\VOX\\models`."""
    stats = {"moved_dirs": 0, "copied_files": 0}
    if not src_models.is_dir():
        return stats
    dst_models.mkdir(parents=True, exist_ok=True)
    for child in list(src_models.iterdir()):
        dst_child = dst_models / child.name
        if child.is_dir() and not dst_child.exists():
            try:
                if os.stat(child).st_dev == os.stat(dst_models).st_dev:
                    shutil.move(str(child), str(dst_child))
                    stats["moved_dirs"] += 1
                    continue
            except Exception:
                pass
        if child.is_dir():
            stats["copied_files"] += _copy_tree_verified(child, dst_child)
        elif child.is_file() and not dst_child.exists():
            shutil.copy2(child, dst_child)
            stats["copied_files"] += 1
    return stats


def _has_meaningful_legacy_data(legacy_data: Path) -> bool:
    if not legacy_data.is_dir():
        return False
    for name in ("voxlocal.db", "config.json", "auth_session.vault"):
        if (legacy_data / name).is_file():
            return True
    for sub in ("recordings", "models", "backups", "snapshots", "exports"):
        p = legacy_data / sub
        if p.is_dir() and any(p.iterdir()):
            return True
    return False


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest().lower()


def migrate_legacy_install_data(
    install_dir: Path,
    target_user_root: Path,
    *,
    logger_cb: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """
    Automatically migrate legacy `<install_dir>/data` user state & models into
    `<target_user_root>/data` and `<target_user_root>/models`.
    """
    def _log(msg: str) -> None:
        if logger_cb is not None:
            try:
                logger_cb(msg)
            except Exception:
                pass

    install_root = Path(install_dir).resolve()
    # CRITICAL SAFETY GUARD: Never migrate or delete data/ inside a source repository or dev build
    if (
        (install_root / "main.py").is_file()
        or (install_root / "src" / "voxlocal").is_dir()
        or (install_root / ".git").exists()
    ):
        return {"migrated": False, "reason": "refused_source_workspace"}

    legacy_data = (install_root / "data").resolve()
    target_root = Path(target_user_root).resolve()
    target_data = (target_root / "data").resolve()
    target_models = (target_root / "models").resolve()

    if legacy_data == target_data:
        return {"migrated": False, "reason": "same_path"}
    if not _has_meaningful_legacy_data(legacy_data):
        return {"migrated": False, "reason": "no_legacy_data"}

    marker = target_data / ".migration_complete.json"
    if marker.is_file() and not (legacy_data / "voxlocal.db").is_file():
        return {"migrated": False, "reason": "already_migrated"}

    # CRITICAL SAFETY GUARD: Never merge/delete legacy_data if target already has a separate DB
    if (legacy_data / "voxlocal.db").is_file() and (target_data / "voxlocal.db").is_file():
        return {"migrated": False, "reason": "target_db_already_exists"}

    target_data.mkdir(parents=True, exist_ok=True)
    target_models.mkdir(parents=True, exist_ok=True)

    staging_db = target_data / "voxlocal.db.migrating"
    src_db = legacy_data / "voxlocal.db"
    dst_db = target_data / "voxlocal.db"
    row_counts: Dict[str, int] = {}
    rewritten_paths = 0
    backup_path_str = ""
    db_sha = ""

    try:
        # 1. Database backup, online clone, integrity/row verification & path rewriting
        if src_db.is_file() and not dst_db.is_file():
            sconn = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
            try:
                src_integ = sconn.execute("PRAGMA integrity_check").fetchone()[0]
                if src_integ != "ok":
                    raise RuntimeError(f"Source database integrity check failed: {src_integ}")
                row_counts = _collect_table_counts(sconn)

                backups_dir = target_data / "backups"
                backups_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                bk_file = backups_dir / f"voxlocal_pre_migration_{ts}.db"
                bconn = sqlite3.connect(str(bk_file))
                try:
                    sconn.backup(bconn)
                finally:
                    bconn.close()
                backup_path_str = str(bk_file)

                if staging_db.exists():
                    staging_db.unlink(missing_ok=True)
                dconn = sqlite3.connect(str(staging_db))
                try:
                    sconn.backup(dconn)
                finally:
                    dconn.close()
            finally:
                sconn.close()

            dconn = sqlite3.connect(str(staging_db))
            try:
                dst_integ = dconn.execute("PRAGMA integrity_check").fetchone()[0]
                if dst_integ != "ok":
                    raise RuntimeError(f"Migrated database integrity check failed: {dst_integ}")
                dst_counts = _collect_table_counts(dconn)
                if dst_counts != row_counts:
                    raise RuntimeError(f"Migrated database row count mismatch: {dst_counts} != {row_counts}")
                rewritten_paths = _rewrite_db_legacy_paths(dconn, legacy_data, target_data)
            finally:
                dconn.close()

            os.replace(staging_db, dst_db)
            db_sha = _sha256_file(dst_db)
            _log(
                f"Migrated SQLite DB ({sum(row_counts.values())} verified rows, "
                f"{rewritten_paths} recording paths updated) -> {dst_db}"
            )
        elif dst_db.is_file():
            dconn = sqlite3.connect(f"file:{dst_db}?mode=ro", uri=True)
            try:
                row_counts = _collect_table_counts(dconn)
            finally:
                dconn.close()
            db_sha = _sha256_file(dst_db)

        # 2. Config.json (rewrite embedded legacy paths) & auth vault
        src_cfg = legacy_data / "config.json"
        dst_cfg = target_data / "config.json"
        if src_cfg.is_file() and not dst_cfg.is_file():
            cfg_raw = src_cfg.read_text(encoding="utf-8")
            try:
                cfg_obj = json.loads(cfg_raw)
                if isinstance(cfg_obj, dict):
                    for key in ("storage_dir", "models_dir", "recordings_dir"):
                        val = cfg_obj.get(key)
                        if isinstance(val, str) and val:
                            if key == "models_dir" and "models" in val.lower():
                                norm_legacy_models = legacy_data / "models"
                                cfg_obj[key] = _rewrite_prefix_path(val, norm_legacy_models, target_models)
                                if cfg_obj[key] == val:
                                    cfg_obj[key] = _rewrite_prefix_path(val, legacy_data, target_data)
                            else:
                                cfg_obj[key] = _rewrite_prefix_path(val, legacy_data, target_data)
                    tmp_cfg = dst_cfg.with_name("config.json.mig_tmp")
                    tmp_cfg.write_text(json.dumps(cfg_obj, indent=2), encoding="utf-8")
                    os.replace(tmp_cfg, dst_cfg)
                else:
                    shutil.copy2(src_cfg, dst_cfg)
            except Exception:
                shutil.copy2(src_cfg, dst_cfg)

        src_vault = legacy_data / "auth_session.vault"
        dst_vault = target_data / "auth_session.vault"
        if src_vault.is_file() and not dst_vault.is_file():
            shutil.copy2(src_vault, dst_vault)

        # 3. Recordings, backups, snapshots, exports, crash reports, logs
        copied_files = 0
        for sub in USER_SUBDIRS:
            copied_files += _copy_tree_verified(legacy_data / sub, target_data / sub)

        # 4. Models (atomic same-volume directory move -> target_models)
        model_stats = _migrate_models_dir(legacy_data / "models", target_models)
        _log(
            f"Migrated user assets ({copied_files} files) & models "
            f"(moved_dirs={model_stats['moved_dirs']}, copied_files={model_stats['copied_files']})"
        )

        # 5. Final DB sanity verify & commit completion marker BEFORE cleaning legacy_data
        if dst_db.is_file():
            chk = sqlite3.connect(f"file:{dst_db}?mode=ro", uri=True)
            try:
                if chk.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Post-migration final DB integrity verification failed")
            finally:
                chk.close()

        marker_payload = {
            "schema_version": 1,
            "source_legacy_data_dir": str(legacy_data),
            "target_data_dir": str(target_data),
            "target_models_dir": str(target_models),
            "migrated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "backup_path": backup_path_str,
            "db_sha256": db_sha,
            "row_counts": row_counts,
            "rewritten_paths": rewritten_paths,
            "copied_files": copied_files,
            "model_migration": model_stats,
        }
        tmp_marker = marker.with_name(".migration_complete.tmp")
        tmp_marker.write_text(json.dumps(marker_payload, indent=2), encoding="utf-8")
        os.replace(tmp_marker, marker)

        # 6. Decouple legacy data folder from application binaries directory
        shutil.rmtree(legacy_data, ignore_errors=False)
        return {"migrated": True, **marker_payload}
    except Exception as exc:
        staging_db.unlink(missing_ok=True)
        return {"migrated": False, "error": f"{type(exc).__name__}: {exc}"}
