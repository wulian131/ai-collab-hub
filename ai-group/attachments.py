"""Bounded, authenticated attachment storage; content never served inline."""
import json
import re
import time
import threading
import uuid
from urllib.parse import parse_qs, quote

MAX_FILE = 10 * 1024 * 1024
CHUNK = 512 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_PENDING = 50 * 1024 * 1024


class Attachments:
    def __init__(self, room, error):
        self.room, self.error = room, error
        self.download_slots = threading.BoundedSemaphore(2)
        with room.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS attachments (
                id TEXT PRIMARY KEY, author TEXT NOT NULL, conversation_id TEXT NOT NULL,
                name TEXT NOT NULL, size INTEGER NOT NULL, data BLOB NOT NULL,
                created REAL NOT NULL, message_id INTEGER)''')
            db.execute('CREATE INDEX IF NOT EXISTS attachments_message ON attachments(message_id)')
            if 'attachments' not in {r['name'] for r in db.execute('PRAGMA table_info(messages)')}:
                db.execute("ALTER TABLE messages ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]'")

    @staticmethod
    def metadata(row):
        return {k: row[k] for k in ('id', 'name', 'size', 'conversation_id')}

    def create(self, author, payload):
        identity, name, size = (payload.get(k) for k in ('id', 'name', 'size'))
        try:
            if str(uuid.UUID(identity)) != identity:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise self.error(400, '附件标识无效')
        if not isinstance(name, str) or not name.strip() or len(name) > 240:
            raise self.error(400, '文件名需包含 1–240 个字符')
        name = re.sub(r'[\x00-\x1f\x7f/\\]', '_', name).strip()
        if type(size) is not int or not 0 <= size <= MAX_FILE:
            raise self.error(413, '单个附件不能超过 10 MB')
        with self.room.db() as db:
            db.execute('BEGIN IMMEDIATE')
            conversation = self.room.conversation(db, payload.get('conversation_id', 'main'))
            # Unsent uploads expire after seven days; bound abandoned uploads too.
            db.execute('DELETE FROM attachments WHERE message_id IS NULL AND created<?', (time.time()-7*86400,))
            old = db.execute('SELECT id,author,conversation_id,name,size,message_id,length(data) AS offset FROM attachments WHERE id=?', (identity,)).fetchone()
            if old:
                if (old['author'], old['conversation_id'], old['name'], old['size']) != (author, conversation, name, size):
                    raise self.error(409, '附件标识已被使用')
                return {'attachment': self.metadata(old), 'offset': old['offset']}
            total = db.execute('SELECT COALESCE(SUM(size),0),COUNT(*) FROM attachments').fetchone()
            pending = db.execute('SELECT COALESCE(SUM(size),0),COUNT(*) FROM attachments WHERE author=? AND message_id IS NULL', (author,)).fetchone()
            if total[0]+size > MAX_TOTAL or total[1] >= 10000:
                raise self.error(413, '附件存储空间已满，请联系管理员')
            if pending[0]+size > MAX_PENDING or pending[1] >= 100:
                raise self.error(413, '待发送附件过多，请移除不需要的附件后重试')
            db.execute('INSERT INTO attachments VALUES(?,?,?,?,?,?,?,NULL)', (identity, author, conversation, name, size, b'', time.time()))
        return {'attachment': {'id': identity, 'name': name, 'size': size, 'conversation_id': conversation}, 'offset': 0}

    def upload(self, author, identity, offset, data):
        with self.room.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT id,author,conversation_id,name,size,message_id,length(data) AS offset FROM attachments WHERE id=?', (identity,)).fetchone()
            if not row or row['author'] != author:
                raise self.error(404, '附件不存在')
            if row['message_id'] is not None:
                raise self.error(409, '已发送的附件不能修改')
            if offset < 0 or not 0 < len(data) <= CHUNK or offset+len(data) > row['size']:
                raise self.error(400, '附件分片大小或位置无效')
            if offset < row['offset'] and db.execute('SELECT substr(data,?,?) FROM attachments WHERE id=?', (offset+1, len(data), identity)).fetchone()[0] == data:
                return {'offset': row['offset']}
            if offset != row['offset']:
                raise self.error(409, '上传位置不一致，请重新选择文件')
            db.execute('UPDATE attachments SET data=CAST(data || ? AS BLOB) WHERE id=?', (data, identity))
        return {'offset': offset+len(data)}

    def bind(self, db, author, conversation, message_id, identities):
        if not isinstance(identities, list) or len(identities) > 5 or any(not isinstance(x, str) for x in identities) or len(set(identities)) != len(identities):
            raise self.error(400, '每条消息最多包含 5 个不同附件')
        metadata = []
        for identity in identities:
            row = db.execute('SELECT id,author,conversation_id,name,size,message_id,length(data) AS offset FROM attachments WHERE id=?', (identity,)).fetchone()
            if not row or row['author'] != author:
                raise self.error(404, '附件不存在或不属于当前身份')
            if row['conversation_id'] != conversation or row['message_id'] is not None:
                raise self.error(409, '附件不能跨会话或重复发送')
            if row['offset'] != row['size']:
                raise self.error(409, '附件尚未上传完成')
            metadata.append(self.metadata(row))
            db.execute('UPDATE attachments SET message_id=? WHERE id=?', (message_id, identity))
        db.execute('UPDATE messages SET attachments=? WHERE id=?', (json.dumps(metadata, ensure_ascii=False), message_id))

    def handle(self, handler, parsed, post, author, readonly):
        if post:
            if readonly:
                raise self.error(403, '只读身份不能上传附件')
            self.room.devices.permit(author, 'execute')
            origin = handler.headers.get('Origin')
            # Bearer auth is mandatory; additionally reject cross-origin browsers.
            from urllib.parse import urlsplit
            import os
            expected = os.environ.get('AICOLLABHUB_PUBLIC_ORIGIN', '').rstrip('/')
            if origin and ((expected and origin != expected) or (not expected and urlsplit(origin).netloc != handler.headers.get('Host'))):
                raise self.error(403, '上传请求必须来自本站')
            length = int(handler.headers.get('Content-Length', '0'))
            if not 0 < length <= CHUNK:
                raise self.error(413, '上传分片不能超过 512 KB')
            data = bytearray()
            deadline = time.monotonic()+30
            while len(data) < length:
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise self.error(408, '上传超时，请重试')
                handler.connection.settimeout(min(15, remaining))
                try:
                    part = handler.rfile.read(length-len(data))
                except TimeoutError:
                    raise self.error(408, '上传超时，请重试')
                if not part:
                    break
                data.extend(part)
            data = bytes(data)
            if len(data) != length:
                raise self.error(400, '上传中断，请重试')
            if parsed.path == '/api/attachments':
                import json
                payload = json.loads(data)
                if not isinstance(payload, dict):
                    raise self.error(400, '附件信息格式无效')
                result = self.create(author, payload)
            elif parsed.path.endswith('/remove'):
                identity = parsed.path.split('/')[-2]
                with self.room.db() as db:
                    db.execute('DELETE FROM attachments WHERE id=? AND author=? AND message_id IS NULL', (identity, author))
                result = {'ok': True}
            else:
                identity = parsed.path.split('/')[-1]
                offset = int(parse_qs(parsed.query).get('offset', ['-1'])[0])
                result = self.upload(author, identity, offset, data)
            return handler.respond(200, result)
        if not self.download_slots.acquire(blocking=False):
            raise self.error(429, '下载繁忙，请稍后重试')
        try:
            identity = parsed.path.split('/')[-1]
            with self.room.db() as db:
                row = db.execute('SELECT * FROM attachments WHERE id=?', (identity,)).fetchone()
            if not row or (row['message_id'] is None and row['author'] != author):
                raise self.error(404, '附件不存在')
            if len(row['data']) != row['size']:
                raise self.error(409, '附件尚未上传完成')
            return handler.respond(200, row['data'], 'application/octet-stream', {
                'Content-Disposition': "attachment; filename=download; filename*=UTF-8''"+quote(row['name'], safe='')})
        finally:
            self.download_slots.release()
