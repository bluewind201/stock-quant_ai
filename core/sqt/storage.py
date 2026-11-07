"""Keep a running SQLite database off the source-code sync directory on Windows."""
import hashlib
import json
import os
import sqlite3
import uuid
from pathlib import Path


def prepare_storage(project_root):
    explicit = os.getenv('SQT_DB_PATH')
    if explicit:
        return explicit, ''
    legacy = Path(project_root)/'stock-data'/'stock.sqlite3'
    if os.name != 'nt':
        return str(legacy), ''
    identity = hashlib.sha256(str(Path(project_root).resolve()).lower().encode()).hexdigest()[:10]
    folder = Path(os.getenv('LOCALAPPDATA',str(Path.home()/'AppData'/'Local')))/'StockQuant'/identity
    folder.mkdir(parents=True,exist_ok=True)
    destination = folder/'stock.sqlite3'
    status_path = folder/'migration.json'
    notice = ''
    if destination.exists():
        if status_path.exists():
            notice = json.loads(status_path.read_text(encoding='utf-8')).get('notice','')
        return str(destination), notice
    if legacy.exists():
        temporary = folder/('migration-'+uuid.uuid4().hex+'.sqlite3')
        source = target = None
        try:
            source = sqlite3.connect(legacy.resolve().as_uri()+'?mode=ro',uri=True,timeout=10)
            if source.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise sqlite3.DatabaseError('Legacy database integrity check failed')
            target = sqlite3.connect(str(temporary))
            source.backup(target)
            if target.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise sqlite3.DatabaseError('Copied database integrity check failed')
            target.close(); target = None
            source.close(); source = None
            os.replace(temporary,destination)
            notice = '기존 훈련 자료를 Windows 로컬 저장소로 복사했습니다. 기존 DB 원본도 보존했습니다.'
        except sqlite3.DatabaseError as exc:
            # A locked or unreadable source must not silently create an empty account.
            code = getattr(exc,'sqlite_errorcode',None)
            if code is not None and (code & 0xff) not in (sqlite3.SQLITE_CORRUPT,sqlite3.SQLITE_NOTADB):
                raise RuntimeError('기존 훈련 DB를 읽을 수 없습니다. 기존 서버를 종료하고 저장소 접근 권한을 확인하세요.') from exc
            notice = ('기존 훈련 DB의 무결성 오류로 자료를 가져오지 못했습니다. 새 로컬 저장소를 사용하며, '
                      '기존 stock-data/stock.sqlite3 원본은 복구를 위해 보존했습니다.')
        finally:
            if source is not None: source.close()
            if target is not None: target.close()
            if temporary.exists(): temporary.unlink()  # only this function's uniquely named copy
        status_path.write_text(json.dumps({'legacy':str(legacy),'notice':notice},ensure_ascii=False),encoding='utf-8')
    return str(destination), notice
