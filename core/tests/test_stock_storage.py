import hashlib
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from sqt.storage import prepare_storage
from sqt.store import Store


@pytest.fixture
def project(tmp_path,monkeypatch):
    monkeypatch.delenv('SQT_DB_PATH',raising=False)
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'local'))
    root=tmp_path/'source'
    root.mkdir()
    return root


def test_explicit_path_is_preserved(project,monkeypatch):
    explicit=str(project/'chosen.sqlite3')
    monkeypatch.setenv('SQT_DB_PATH',explicit)
    assert prepare_storage(project)==(explicit,'')
    assert not Path(explicit).exists()


@pytest.mark.skipif(os.name!='nt',reason='Windows local-storage migration')
def test_healthy_migration_preserves_original_and_existing_destination(project):
    legacy=project/'stock-data'/'stock.sqlite3'
    old=Store(legacy)
    old.put('sessions','one',{'name':'기존 훈련','cash':1234})
    before=hashlib.sha256(legacy.read_bytes()).hexdigest()
    destination,notice=prepare_storage(project)
    assert str(project) not in destination
    migrated=Store(destination)
    assert migrated.get('sessions','one')=={'name':'기존 훈련','cash':1234}
    assert '복사' in notice
    assert hashlib.sha256(legacy.read_bytes()).hexdigest()==before
    migrated.put('sessions','one',{'cash':777})
    assert prepare_storage(project)==(destination,notice)
    assert migrated.get('sessions','one')=={'cash':777}
    assert old.get('sessions','one')['cash']==1234


@pytest.mark.skipif(os.name!='nt',reason='Windows local-storage migration')
def test_corrupt_source_preserved_and_warning_survives_restart(project):
    legacy=project/'stock-data'/'stock.sqlite3'
    legacy.parent.mkdir()
    contents=b'not a sqlite database, preserve for recovery'
    legacy.write_bytes(contents)
    destination,notice=prepare_storage(project)
    assert '무결성 오류' in notice and '보존' in notice
    assert legacy.read_bytes()==contents
    assert not Path(destination).exists()
    fresh=Store(destination)
    assert fresh.all('sessions')==[]
    assert prepare_storage(project)==(destination,notice)
    assert not list(Path(destination).parent.glob('migration-*.sqlite3'))


@pytest.mark.skipif(os.name!='nt',reason='Windows local-storage migration')
def test_storage_without_legacy_stays_outside_project(project):
    destination,notice=prepare_storage(project)
    assert str(project) not in destination and notice==''
    assert not (project/'stock-data').exists()


@pytest.mark.skipif(os.name!='nt',reason='Windows local-storage migration')
def test_locked_source_does_not_fall_back_to_fresh_database(project,monkeypatch):
    legacy=project/'stock-data'/'stock.sqlite3'
    legacy.parent.mkdir()
    legacy.write_bytes(b'preserve original')
    def locked(*args,**kwargs):
        error=sqlite3.OperationalError('database is locked')
        error.sqlite_errorcode=sqlite3.SQLITE_BUSY
        raise error
    monkeypatch.setattr(sqlite3,'connect',locked)
    with pytest.raises(RuntimeError,match='기존 서버를 종료'):
        prepare_storage(project)
    local=Path(os.environ['LOCALAPPDATA'])
    assert not list(local.rglob('stock.sqlite3'))
    assert not list(local.rglob('migration.json'))
    assert legacy.read_bytes()==b'preserve original'


def test_concurrent_transactions_and_rollback_remain_consistent(tmp_path):
    path=tmp_path/'concurrent.sqlite3'
    first,second=Store(path),Store(path)
    first.put('counter','one',{'value':0})
    def update(index):
        db=first if index%2 else second
        for _ in range(15):
            with db.connect(write=True) as con:
                value=db.get('counter','one',con)['value']
                db.put('counter','one',{'value':value+1},con)
            assert db.get('counter','one')['value']>=1
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(update,range(6)))
    assert first.get('counter','one')['value']==90
    with pytest.raises(RuntimeError,match='rollback'):
        with first.connect(write=True) as con:
            first.put('counter','one',{'value':-999},con)
            raise RuntimeError('rollback')
    assert second.get('counter','one')['value']==90
    with first.connect() as con:
        assert con.execute('PRAGMA quick_check').fetchall()==[('ok',)]
        assert con.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
