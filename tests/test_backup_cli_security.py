import importlib.machinery
import importlib.util
import io
import json
import sqlite3
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_backup_cli():
    path = Path(__file__).resolve().parent.parent / "scripts" / "cleverly-backup"
    loader = importlib.machinery.SourceFileLoader("cleverly_backup_under_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _patch_repo(module, monkeypatch, root: Path):
    monkeypatch.setattr(module, "_REPO_ROOT", root)
    monkeypatch.setattr(module, "_DATA_DIR", root / "data")
    monkeypatch.setattr(module, "_BACKUP_DIR", root / "backups")


def _restore_args(path: Path):
    return SimpleNamespace(path=str(path), yes=True, pretty=False)


def _verify_args(path: Path):
    return SimpleNamespace(path=str(path), pretty=False)


def _snapshot_args(out: Path | None = None, *, include_research=False, include_attachments=False):
    return SimpleNamespace(
        out=str(out) if out else None,
        include_research=include_research,
        include_attachments=include_attachments,
        pretty=False,
    )


def _list_args():
    return SimpleNamespace(pretty=False)


def test_restore_rejects_symlink_escape(tmp_path, monkeypatch):
    backup = _load_backup_cli()
    repo = tmp_path / "repo"
    data = repo / "data"
    outside = tmp_path / "outside"
    data.mkdir(parents=True)
    outside.mkdir()
    (data / "keep.txt").write_text("still here", encoding="utf-8")
    _patch_repo(backup, monkeypatch, repo)

    tar_path = tmp_path / "malicious.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        data_dir = tarfile.TarInfo("data")
        data_dir.type = tarfile.DIRTYPE
        tar.addfile(data_dir)

        link = tarfile.TarInfo("data/link")
        link.type = tarfile.SYMTYPE
        link.linkname = str(outside)
        tar.addfile(link)

        payload = b"escaped"
        escaped = tarfile.TarInfo("data/link/pwned.txt")
        escaped.size = len(payload)
        tar.addfile(escaped, io.BytesIO(payload))

    with pytest.raises(SystemExit):
        backup.cmd_restore(_restore_args(tar_path))

    assert not (outside / "pwned.txt").exists()
    assert (data / "keep.txt").read_text(encoding="utf-8") == "still here"


def test_verify_rejects_symlink_escape(tmp_path):
    backup = _load_backup_cli()

    tar_path = tmp_path / "malicious.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        link = tarfile.TarInfo("data/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/tmp"
        tar.addfile(link)

    with pytest.raises(SystemExit):
        backup.cmd_verify(_verify_args(tar_path))


def test_restore_rejects_hardlink_entries(tmp_path, monkeypatch):
    backup = _load_backup_cli()
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    _patch_repo(backup, monkeypatch, repo)

    tar_path = tmp_path / "hardlink.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        link = tarfile.TarInfo("data/hardlink")
        link.type = tarfile.LNKTYPE
        link.linkname = "../outside.txt"
        tar.addfile(link)

    with pytest.raises(SystemExit):
        backup.cmd_restore(_restore_args(tar_path))


def test_restore_extracts_regular_files_without_extractall(tmp_path, monkeypatch):
    backup = _load_backup_cli()
    repo = tmp_path / "repo"
    data = repo / "data"
    data.mkdir(parents=True)
    (data / "old.txt").write_text("old", encoding="utf-8")
    _patch_repo(backup, monkeypatch, repo)

    tar_path = tmp_path / "valid.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        folder = tarfile.TarInfo("data/nested")
        folder.type = tarfile.DIRTYPE
        tar.addfile(folder)

        payload = b"new"
        item = tarfile.TarInfo("data/nested/new.txt")
        item.size = len(payload)
        tar.addfile(item, io.BytesIO(payload))

    backup.cmd_restore(_restore_args(tar_path))

    assert (repo / "data" / "nested" / "new.txt").read_text(encoding="utf-8") == "new"
    assert not (repo / "data" / "old.txt").exists()
    assert list(repo.glob("data.before-restore-*"))


def test_snapshot_list_and_verify_round_trip(tmp_path, monkeypatch, capsys):
    backup = _load_backup_cli()
    repo = tmp_path / "repo"
    data = repo / "data"
    data.mkdir(parents=True)
    _patch_repo(backup, monkeypatch, repo)

    (data / "notes.txt").write_text("private notes", encoding="utf-8")
    (data / "deep_research").mkdir()
    (data / "deep_research" / "run.txt").write_text("large", encoding="utf-8")
    (data / "mail-attachments").mkdir()
    (data / "mail-attachments" / "cached.txt").write_text("cache", encoding="utf-8")
    conn = sqlite3.connect(data / "app.db")
    conn.execute("create table sample (value text)")
    conn.execute("insert into sample values ('ok')")
    conn.commit()
    conn.close()

    backup.cmd_list(_list_args())
    assert json.loads(capsys.readouterr().out) == []

    out_path = tmp_path / "snapshot.tar.gz"
    backup.cmd_snapshot(_snapshot_args(out_path))
    snapshot = json.loads(capsys.readouterr().out)
    assert snapshot["ok"] is True
    assert snapshot["included_research"] is False
    assert snapshot["included_attachments"] is False

    with tarfile.open(out_path, "r:gz") as tar:
        names = set(tar.getnames())
    assert "data/notes.txt" in names
    assert "data/app.db" in names
    assert "data/deep_research/run.txt" not in names
    assert "data/mail-attachments/cached.txt" not in names

    backup.cmd_verify(_verify_args(out_path))
    verified = json.loads(capsys.readouterr().out)
    assert verified["ok"] is True
    assert verified["members"] == len(names)

    backup.cmd_snapshot(_snapshot_args(include_research=True, include_attachments=True))
    default_snapshot = json.loads(capsys.readouterr().out)
    assert Path(default_snapshot["path"]).parent == repo / "backups"

    (repo / "backups" / "not-a-file").mkdir()
    backup.cmd_list(_list_args())
    listed = json.loads(capsys.readouterr().out)
    assert listed[0]["name"].startswith("cleverly-backup-")


def test_backup_failure_and_validation_branches(tmp_path, monkeypatch):
    backup = _load_backup_cli()
    repo = tmp_path / "repo"
    _patch_repo(backup, monkeypatch, repo)

    with pytest.raises(SystemExit):
        backup.cmd_snapshot(_snapshot_args(tmp_path / "missing.tar.gz"))

    with pytest.raises(SystemExit):
        backup.cmd_verify(_verify_args(tmp_path / "missing.tar.gz"))

    corrupt = tmp_path / "corrupt.tar.gz"
    corrupt.write_text("not a tarball", encoding="utf-8")
    with pytest.raises(SystemExit):
        backup.cmd_verify(_verify_args(corrupt))

    with pytest.raises(SystemExit):
        backup.cmd_restore(SimpleNamespace(path=str(tmp_path / "missing.tar.gz"), yes=True, pretty=False))

    empty_tar = tmp_path / "empty.tar.gz"
    with tarfile.open(empty_tar, "w:gz"):
        pass
    with pytest.raises(SystemExit):
        backup.cmd_restore(SimpleNamespace(path=str(empty_tar), yes=False, pretty=False))

    outside = tarfile.TarInfo("other/file.txt")
    with pytest.raises(SystemExit):
        backup._validate_restore_members([outside])

    parent = tarfile.TarInfo("data/../file.txt")
    with pytest.raises(SystemExit):
        backup._validate_restore_members([parent])

    special = tarfile.TarInfo("data/device")
    special.type = tarfile.CHRTYPE
    with pytest.raises(SystemExit):
        backup._validate_restore_members([special])

    restore_tar = tmp_path / "restore.tar.gz"
    with tarfile.open(restore_tar, "w:gz") as tar:
        member = tarfile.TarInfo("data/file.txt")
        payload = b"data"
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))
    monkeypatch.setattr(backup, "_extract_restore_members", lambda *_args: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(SystemExit):
        backup.cmd_restore(_restore_args(restore_tar))


def test_snapshot_continues_when_size_accounting_fails(tmp_path, monkeypatch, capsys):
    backup = _load_backup_cli()
    repo = tmp_path / "repo"
    data = repo / "data"
    data.mkdir(parents=True)
    _patch_repo(backup, monkeypatch, repo)
    notes = data / "notes.txt"
    notes.write_text("private notes", encoding="utf-8")
    out_path = tmp_path / "snapshot.tar.gz"
    stat_failures_enabled = set()

    class FakeTar:
        def __init__(self, path):
            self.path = Path(path)

        def __enter__(self):
            self.path.write_bytes(b"tar")
            return self

        def __exit__(self, *_exc):
            return False

        def add(self, source, arcname):
            stat_failures_enabled.add(Path(source))

    real_stat = backup.Path.stat

    def flaky_stat(self, *args, **kwargs):
        if self in stat_failures_enabled:
            raise OSError("unreadable size")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(backup.tarfile, "open", lambda path, mode: FakeTar(path))
    monkeypatch.setattr(backup.Path, "stat", flaky_stat)

    backup.cmd_snapshot(_snapshot_args(out_path))
    snapshot = json.loads(capsys.readouterr().out)
    assert snapshot["ok"] is True
    assert snapshot["uncompressed_bytes"] == 0


def test_sqlite_copy_fallback_extract_error_and_parser(tmp_path):
    backup = _load_backup_cli()
    src = tmp_path / "notreally.db"
    dst = tmp_path / "copy.db"
    src.write_text("not sqlite", encoding="utf-8")
    backup._sqlite_safe_copy(src, dst)
    assert dst.read_text(encoding="utf-8") == "not sqlite"

    class EmptyTar:
        def extractfile(self, _member):
            return None

    member = tarfile.TarInfo("data/file.txt")
    member.size = 4
    with pytest.raises(SystemExit):
        backup._extract_restore_members(EmptyTar(), [member], tmp_path)

    parser = backup._build_parser()
    snapshot = parser.parse_args(["snapshot", "--out", "x.tar.gz", "--include-research"])
    assert snapshot.cmd == "snapshot"
    assert snapshot.out == "x.tar.gz"
    assert snapshot.include_research is True
    assert parser.parse_args(["list"]).func is backup.cmd_list
    assert parser.parse_args(["verify", "x.tar.gz"]).func is backup.cmd_verify
    assert parser.parse_args(["restore", "x.tar.gz", "--yes"]).yes is True
