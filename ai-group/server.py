#!/usr/bin/env python3
"""A single-room, identity-authenticated HTTP message hub. No third-party packages."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import re
import select
import socket
import sqlite3
import time
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from notifications import accept_key, frame, read_frame
from devices import DeviceRegistry
from attachments import Attachments


class APIError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Room:
    def __init__(self, database, config):
        self.database = str(database)
        self.members = {m['id']: m for m in config['members']}
        self.coordinator = config.get('coordinator')
        self._listeners = {}
        self._listener_lock = threading.Lock()
        self._lease_timers = {}
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS messages (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, author TEXT NOT NULL,
                  text TEXT NOT NULL, target TEXT, reply_to INTEGER,
                  depth INTEGER NOT NULL, created REAL NOT NULL, key TEXT,
                  UNIQUE(author,key));
                CREATE TABLE IF NOT EXISTS jobs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER UNIQUE NOT NULL,
                  target TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                  lease TEXT, lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0,
                  reply_id INTEGER, finished REAL, parent_job INTEGER,
                  kind TEXT NOT NULL DEFAULT 'task');
                CREATE TABLE IF NOT EXISTS presence (member TEXT PRIMARY KEY, seen REAL NOT NULL, detail TEXT);
            ''')
            # Allow upgrading an already-created first-trial database.
            columns = {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
            if 'parent_job' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN parent_job INTEGER')
            if 'kind' not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'task'")

            message_columns = {row['name'] for row in db.execute('PRAGMA table_info(messages)')}
            if 'targets' not in message_columns:
                db.execute("ALTER TABLE messages ADD COLUMN targets TEXT NOT NULL DEFAULT '[]'")
            legacy_unique = any(row['unique'] and
                [col['name'] for col in db.execute('PRAGMA index_info(' + row['name'] + ')')] == ['message_id']
                for row in db.execute('PRAGMA index_list(jobs)'))
            if legacy_unique:
                # Preserve primary keys, parent links, leases and completed results.
                db.executescript('''BEGIN IMMEDIATE;
                    CREATE TABLE jobs_multi (
                      id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER NOT NULL,
                      target TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                      lease TEXT, lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0,
                      reply_id INTEGER, finished REAL, parent_job INTEGER,
                      kind TEXT NOT NULL DEFAULT 'task', UNIQUE(message_id,target));
                    INSERT INTO jobs_multi SELECT id,message_id,target,status,lease,lease_until,
                        attempts,reply_id,finished,parent_job,kind FROM jobs;
                    DROP TABLE jobs;
                    ALTER TABLE jobs_multi RENAME TO jobs;
                    COMMIT;''')
            db.execute("CREATE TABLE IF NOT EXISTS collaborations (id INTEGER PRIMARY KEY, participants TEXT NOT NULL, leader TEXT, status TEXT NOT NULL DEFAULT 'active', round INTEGER NOT NULL DEFAULT 0)")
            for table in ('messages', 'collaborations'):
                if 'execution' not in {r['name'] for r in db.execute('PRAGMA table_info('+table+')')}:
                    db.execute("ALTER TABLE " + table + " ADD COLUMN execution TEXT NOT NULL DEFAULT '{}'")
            if 'source' not in {r['name'] for r in db.execute('PRAGMA table_info(messages)')}:
                db.execute("ALTER TABLE messages ADD COLUMN source TEXT NOT NULL DEFAULT 'legacy'")
            columns = {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
            for name, definition in [('team_id', 'INTEGER'), ('completed', 'INTEGER NOT NULL DEFAULT 1'), ('blocked', 'INTEGER NOT NULL DEFAULT 0')]:
                if name not in columns:
                    db.execute('ALTER TABLE jobs ADD COLUMN ' + name + ' ' + definition)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, title TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL)")
            columns = {row['name'] for row in db.execute('PRAGMA table_info(conversations)')}
            if 'participants' not in columns:
                db.execute('ALTER TABLE conversations ADD COLUMN participants TEXT')
            db.execute("INSERT OR IGNORE INTO conversations(id,title,created,updated) VALUES('main','默认历史会话',?,?)", (time.time(), time.time()))
            for table in ('messages', 'jobs'):
                columns = {r['name'] for r in db.execute('PRAGMA table_info('+table+')')}
                if 'conversation_id' not in columns:
                    db.execute("ALTER TABLE " + table + " ADD COLUMN conversation_id TEXT NOT NULL DEFAULT 'main'")
            if 'request_hash' not in {r['name'] for r in db.execute('PRAGMA table_info(messages)')}:
                db.execute('ALTER TABLE messages ADD COLUMN request_hash TEXT')
            db.execute('CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id,id)')
            db.execute('CREATE INDEX IF NOT EXISTS jobs_conversation ON jobs(conversation_id,id)')
            db.execute("CREATE TABLE IF NOT EXISTS worker_controls (member TEXT PRIMARY KEY, paused INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '')")
            db.execute('CREATE TABLE IF NOT EXISTS job_recoveries (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, actor TEXT NOT NULL, previous_status TEXT NOT NULL, previous_reply_id INTEGER, reason TEXT NOT NULL, created REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS job_dependencies (job_id INTEGER PRIMARY KEY, team_id INTEGER NOT NULL)')
            db.execute("CREATE TABLE IF NOT EXISTS job_events (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, state TEXT NOT NULL, created REAL NOT NULL)")
            db.execute('CREATE INDEX IF NOT EXISTS events_job ON job_events(job_id,id)')
            # Triggers cover legacy, summary and continuation INSERTs as well as new clients.
            db.execute("""CREATE TRIGGER IF NOT EXISTS message_conversation AFTER INSERT ON messages BEGIN
                UPDATE messages SET conversation_id=COALESCE((SELECT conversation_id FROM messages WHERE id=NEW.reply_to),NEW.conversation_id) WHERE id=NEW.id;
                UPDATE conversations SET updated=NEW.created WHERE id=(SELECT conversation_id FROM messages WHERE id=NEW.id);
            END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS job_conversation AFTER INSERT ON jobs BEGIN
                UPDATE jobs SET conversation_id=(SELECT conversation_id FROM messages WHERE id=NEW.message_id) WHERE id=NEW.id;
                INSERT INTO job_events(job_id,state,created) VALUES(NEW.id,'queued',CAST(strftime('%s','now') AS REAL));
            END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS job_progress AFTER UPDATE OF status ON jobs WHEN OLD.status != NEW.status BEGIN
                INSERT INTO job_events(job_id,state,created) VALUES(NEW.id,CASE NEW.status WHEN 'running' THEN 'claimed' ELSE NEW.status END,CAST(strftime('%s','now') AS REAL));
            END""")
        self.attachments = Attachments(self, APIError)
        self.devices = DeviceRegistry(self, APIError)
        with self.db() as db:
            running = list(db.execute("SELECT id,lease_until FROM jobs WHERE status='running'"))
        for row in running:
            self._arm_lease(row['id'], row['lease_until'])

    @staticmethod
    def public_message(row):
        message = dict(row)
        message.pop('request_hash', None)
        message['attachments'] = json.loads(message.get('attachments', '[]'))
        message['source'] = message.get('source', 'legacy')
        if message['author'] != 'owner':
            message['source'] = 'system' if message['author'] == 'hub' else 'agent'
        message['execution'] = json.loads(message.get('execution', '{}'))
        message['targets'] = json.loads(message.get('targets', '[]')) or ([message['target']] if message.get('target') else [])
        return message

    def mentioned(self, text):
        found = []
        spans = []
        for member in self.members.values():
            if member.get('state') in ('archived','deleted'):continue
            names = [member['id'], member['name']] + member.get('aliases', [])
            pattern = r'(?<![\w@])@(?:' + '|'.join(re.escape(n) for n in sorted(set(names), key=len, reverse=True)) + r')(?=$|[\s,，、:：;；.!！?？])'
            for match in re.finditer(pattern, text, re.IGNORECASE):
                found.append((match.start(), member['id']))
                spans.append(match.span())
        for match in re.finditer(r'(?<![\w@])@([A-Za-z0-9_-]+)', text):
            if not any(start <= match.start() < end for start, end in spans):
                raise APIError(400, '未找到被 @ 的成员：' + match.group(1))
        return list(dict.fromkeys(identity for _, identity in sorted(found)))

    def paused(self, author):
        with self.db() as db:
            row = db.execute('SELECT paused FROM worker_controls WHERE member=?',(author,)).fetchone()
        return bool(row and row['paused'])

    def has_job(self, author):
        if self.paused(author): return False
        if self.members[author]['role'] != 'agent' or not self.members[author].get('enabled', True):
            return False
        if self.members[author].get('permissions', {}).get('execute') is False:
            return False
        with self.db() as db:
            return db.execute("SELECT 1 FROM jobs WHERE target=? AND (status='pending' OR (status='running' AND lease_until<?)) AND NOT EXISTS (SELECT 1 FROM job_dependencies d LEFT JOIN collaborations c ON c.id=d.team_id WHERE d.job_id=jobs.id AND COALESCE(c.status,'missing') != 'done') LIMIT 1", (author, time.time())).fetchone() is not None

    def notify(self, exclude=None, observers_only=False):
        with self._listener_lock:
            for listener, (identity, writer) in self._listeners.items():
                if listener is exclude or (observers_only and self.members[identity]['role'] != 'human'):
                    continue
                try:
                    writer.send(b'x')
                except (BlockingIOError, OSError):
                    pass

    def subscribe(self, author):
        with self._listener_lock:
            if len(self._listeners) >= 24 or sum(identity == author for identity, _ in self._listeners.values()) >= 2:
                raise APIError(503, '通知连接已满，请稍后重连')
            reader, writer = socket.socketpair()
            reader.setblocking(False)
            writer.setblocking(False)
            self._listeners[reader] = (author, writer)
        self.notify(exclude=reader)
        return reader

    def unsubscribe(self, reader):
        with self._listener_lock:
            pair = self._listeners.pop(reader, None)
        reader.close()
        if pair:
            pair[1].close()
        self.notify()

    def _arm_lease(self, job_id, until):
        with self._listener_lock:
            previous = self._lease_timers.pop(job_id, None)
            if previous:
                previous.cancel()
            if until is None:
                return
            timer = threading.Timer(max(.01, until - time.time() + .01), self.notify)
            timer.daemon = True
            self._lease_timers[job_id] = timer
            timer.start()

    def close(self):
        with self._listener_lock:
            for timer in self._lease_timers.values():
                timer.cancel()
            self._lease_timers.clear()
            writers = [writer for _, writer in self._listeners.values()]
            self._listeners.clear()
        for writer in writers:
            writer.close()

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.database, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def authenticate(self, token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        self.devices.refresh()
        device = self.devices.authenticate(token)
        if device:
            self.devices.refresh()
            return device, False
        for member in self.members.values():
            if member.get('device'):
                continue
            if secrets.compare_digest(member['token_hash'], digest):
                if not member.get('enabled', True): raise APIError(409, '该成员当前未启用')
                return member['id'], False
            if secrets.compare_digest(member.get('read_token_hash', ''), digest):
                if not member.get('enabled', True): raise APIError(409, '该成员当前未启用')
                return member['id'], True
        raise APIError(401, '身份凭证无效，请重新登录')

    def member_list(self):
        with self.db() as db:
            presence = {row['member']: dict(row) for row in db.execute('SELECT * FROM presence')}
        result = []
        for member in self.members.values():
            public = {k: v for k, v in member.items() if k not in ('token_hash', 'read_token_hash')}
            last = presence.get(member['id'], {}).get('seen')
            grace = 150 if member['id'].startswith('muse-') else 45
            with self._listener_lock:
                listening = any(identity == member['id'] for identity, _ in self._listeners.values())
            public.update(last_seen=last, online=bool(member.get('enabled', True) and (listening or (last and time.time() - last < grace))), notification_connected=listening, activity=presence.get(member['id'], {}).get('detail', ''))
            result.append(public)
        return result

    def ping(self, author, payload):
        self.devices.permit(author, 'read')
        detail = payload.get('detail', 'connected')
        if not isinstance(detail, str) or len(detail) > 100:
            raise APIError(400, '状态格式无效')
        if detail.startswith('processing') and 'job_id' in payload:
            self.progress(author, payload)
        with self.db() as db:
            db.execute('INSERT INTO presence(member,seen,detail) VALUES(?,?,?) ON CONFLICT(member) DO UPDATE SET seen=excluded.seen,detail=excluded.detail',
                       (author, time.time(), detail))
            if detail.startswith('processing') and 'job_id' not in payload:
                db.execute("INSERT INTO job_events(job_id,state,created) SELECT j.id,'processing',? FROM jobs j WHERE j.target=? AND j.status='running' AND j.lease_until>? AND (SELECT count(*) FROM jobs WHERE target=? AND status='running' AND lease_until>?)=1 AND NOT EXISTS(SELECT 1 FROM job_events e WHERE e.job_id=j.id AND e.state='processing')",(time.time(),author,time.time(),author,time.time()))
        self.notify(observers_only=True)
        return {'ok': True}

    def progress(self, author, payload):
        self.devices.permit(author, 'execute')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE id=?', (payload.get('job_id'),)).fetchone()
            if not row or row['target'] != author:
                raise APIError(403, '只能报告自己的任务进度')
            lease = payload.get('lease')
            if not isinstance(lease, str) or not secrets.compare_digest(row['lease'] or '', lease) or row['status'] != 'running' or row['lease_until'] < time.time():
                raise APIError(409, '任务领取凭证已失效')
            db.execute("INSERT INTO job_events(job_id,state,created) SELECT ?,'processing',? WHERE NOT EXISTS(SELECT 1 FROM job_events WHERE job_id=? AND state='processing')", (row['id'],time.time(),row['id']))
        self.notify(observers_only=True)
        return {'ok': True, 'job_id': row['id'], 'state': 'processing'}

    @staticmethod
    def conversation(db, identity):
        if not isinstance(identity, str) or not db.execute('SELECT 1 FROM conversations WHERE id=?', (identity,)).fetchone():
            raise APIError(404, '会话不存在')
        return identity

    def conversations(self, search='', offset=0, limit=40):
        with self.db() as db:
            rows = db.execute("""SELECT c.*,COALESCE((SELECT substr(text,1,120) FROM messages WHERE conversation_id=c.id ORDER BY id DESC LIMIT 1),'') AS summary
                FROM conversations c WHERE c.title LIKE ? OR EXISTS(SELECT 1 FROM messages WHERE conversation_id=c.id AND text LIKE ?)
                ORDER BY c.updated DESC,c.id LIMIT ? OFFSET ?""", ('%'+search+'%','%'+search+'%',limit+1,offset)).fetchall()
            return {'conversations':[self.public_conversation(r) for r in rows[:limit]],'has_more':len(rows)>limit}

    @staticmethod
    def public_conversation(row):
        result = dict(row)
        result['participants'] = json.loads(result['participants']) if result.get('participants') is not None else None
        return result

    def save_conversation(self, author, payload):
        if self.members[author]['role'] != 'human':
            raise APIError(403, '只有用户可以新建或重命名会话')
        title = payload.get('title', '新对话')
        if not isinstance(title,str) or not 1 <= len(title.strip()) <= 100:
            raise APIError(400, '标题需包含 1–100 个字符')
        identity = payload.get('id')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if payload.get('rename'):
                self.conversation(db, identity)
                db.execute('UPDATE conversations SET title=? WHERE id=?', (title.strip(),identity))
            else:
                if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', identity) or identity=='main':
                    raise APIError(400, '新建会话需要唯一 id')
                participants = payload.get('participants')
                if participants is not None:
                    if not isinstance(participants,list) or not 1 <= len(participants) <= 20 or any(not isinstance(i,str) for i in participants):
                        raise APIError(400, '请选择 1–20 位对话成员')
                    participants = sorted(set(participants))
                previous = db.execute('SELECT * FROM conversations WHERE id=?',(identity,)).fetchone()
                if previous:
                    if participants != self.public_conversation(previous)['participants'] or (participants is not None and previous['title'] != title.strip()):
                        raise APIError(409, '该对话标识已用于不同成员或标题，请用原请求重试')
                else:
                    for member in participants or []:
                        entry = self.members.get(member)
                        if not entry or entry['role'] != 'agent' or not entry.get('enabled',True):
                            raise APIError(400, '对话成员必须是已启用的 Agent')
                        self.devices.permit(member, 'read')
                    db.execute('INSERT INTO conversations(id,title,created,updated,participants) VALUES(?,?,?,?,?)',
                               (identity,title.strip(),time.time(),time.time(),json.dumps(participants) if participants is not None else None))
            result = self.public_conversation(db.execute('SELECT * FROM conversations WHERE id=?',(identity,)).fetchone())
        self.notify()
        return {'conversation':result}

    def messages(self, after=0, limit=100, conversation_id='main', before=0):
        with self.db() as db:
            self.conversation(db, conversation_id)
            if after:
                rows = db.execute('SELECT * FROM messages WHERE conversation_id=? AND id>? ORDER BY id LIMIT ?',
                                  (conversation_id, after, limit)).fetchall()
            else:
                rows = db.execute('SELECT * FROM (SELECT * FROM messages WHERE conversation_id=? AND id<? ORDER BY id DESC LIMIT ?) ORDER BY id',
                                  (conversation_id, before or 9223372036854775807, limit)).fetchall()
            return [self.public_message(r) for r in rows]

    def jobs(self, conversation_id='main', before=0, limit=100):
        with self.db() as db:
            self.conversation(db, conversation_id)
            rows = [dict(row) for row in db.execute("""SELECT id,message_id,target,status,attempts,
                lease_until,reply_id,finished,parent_job,kind,team_id,completed,blocked,conversation_id FROM jobs
                WHERE conversation_id=? AND id<? ORDER BY id DESC LIMIT ?""", (conversation_id,before or 9223372036854775807,limit))]
            for row in rows:
                dependency = db.execute('SELECT d.team_id,c.status FROM job_dependencies d LEFT JOIN collaborations c ON c.id=d.team_id WHERE d.job_id=?',(row['id'],)).fetchone()
                row['dependency'] = dict(dependency) if dependency else None
                row['events'] = [dict(e) for e in db.execute('SELECT state,created FROM job_events WHERE job_id=? ORDER BY id DESC LIMIT 12',(row['id'],))][::-1]
            return rows

    def _insert(self, db, author, payload):
        conversation_id = payload.get('conversation_id')
        if payload.get('reply_to') is not None:
            parent = db.execute('SELECT conversation_id FROM messages WHERE id=?',(payload['reply_to'],)).fetchone()
            if parent:
                if conversation_id is not None and conversation_id != parent['conversation_id']:
                    raise APIError(409, '不能跨会话回复或分派任务')
                conversation_id = parent['conversation_id']
        conversation_id = self.conversation(db, conversation_id or 'main')
        request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        source = payload.get('source', 'legacy')
        if self.members[author]['role'] == 'human':
            if source not in ('user', 'codex', 'chatgpt', 'legacy'):
                raise APIError(400, '消息来源无效')
        else:
            if 'source' in payload:
                raise APIError(403, 'AI 不能指定用户消息来源')
            source = 'agent'
        execution = payload.get('execution', {})
        if not isinstance(execution, dict) or len(execution) > 21:
            raise APIError(400, 'execution 必须是 Codex 设置对象')
        if execution and (self.members[author]['role'] != 'human' or payload.get('broadcast')):
            raise APIError(403, '只有用户分派任务时可以指定模型')
        for identity, settings in execution.items():
            if identity != '*' and (identity not in self.members or not identity.startswith('codex-') or not self.members[identity].get('enabled', True)):
                raise APIError(400, '模型设置只能指定已启用的 Codex')
            if not isinstance(settings, dict) or set(settings) - {'model', 'reasoning_effort'}:
                raise APIError(400, '模型设置只支持 model 和 reasoning_effort')
            if 'model' in settings and (not isinstance(settings['model'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', settings['model'])):
                raise APIError(400, '模型名称无效')
            if 'reasoning_effort' in settings and settings['reasoning_effort'] not in ('none','minimal','low','medium','high','xhigh','max','ultra'):
                raise APIError(400, '思考强度无效')
        if 'dispatch' in payload and not isinstance(payload['dispatch'], bool):
            raise APIError(400, 'dispatch 必须为布尔值')
        text = payload.get('text')
        if not isinstance(text, str) or (not text.strip() and not payload.get('attachments')) or len(text) > 16000:
            raise APIError(400, '消息需包含 1–16000 个字符')
        parent_id = payload.get('reply_to')
        key = payload.get('key') or None
        if key is not None and (not isinstance(key, str) or len(key) > 128):
            raise APIError(400, 'key 应为不超过 128 字符的字符串')
        if key:
            existing = db.execute('SELECT * FROM messages WHERE author=? AND key=?', (author, key)).fetchone()
            if existing:
                if existing['conversation_id'] != conversation_id or existing['text'] != text.strip() or (existing['request_hash'] and existing['request_hash'] != request_hash):
                    raise APIError(409, '重试标识已用于不同内容，请核对原请求')
                return self.public_message(existing)
        targets = payload.get('targets', [])
        if not isinstance(targets, list) or any(not isinstance(t, str) for t in targets):
            raise APIError(400, 'targets 必须是成员 ID 数组')
        targets = list(targets)
        if payload.get('target'):
            if not isinstance(payload['target'], str):
                raise APIError(400, '接收者不存在')
            targets.append(payload['target'])
        if payload.get('broadcast', False):
            targets = []
        elif payload.get('dispatch') is True:
            # Only an explicit dispatch action may interpret mentions as recipients.
            targets += self.mentioned(text)
        targets = list(dict.fromkeys(targets))
        if targets:
            self.devices.permit(author, 'delegate')
        if len(targets) > 20:
            raise APIError(400, '一次最多指定 20 位成员')
        for target_id in targets:
            self.devices.permit(target_id, 'execute')
            if target_id not in self.members:
                raise APIError(400, '接收者不存在')
            if not self.members[target_id].get('enabled', True):
                raise APIError(409, '该成员当前未启用，请先联系管理员启用')
            if target_id == author:
                raise APIError(400, '不能给自己派发任务')
        scope = db.execute('SELECT participants FROM conversations WHERE id=?',(conversation_id,)).fetchone()['participants']
        if scope is not None:
            allowed = set(json.loads(scope))
            if any(target not in allowed for target in targets) or (payload.get('leader') and payload['leader'] not in allowed):
                raise APIError(403, '执行成员或负责人不在当前对话中')
        participants = list(targets)
        leader = None if payload.get('broadcast', False) else payload.get('leader') or None
        if not leader and not payload.get('broadcast', False) and self.members[author]['role'] == 'human':
            for identity in participants:
                names = [identity, self.members[identity]['name']] + self.members[identity].get('aliases', [])
                if any(re.search(r'@?' + re.escape(name) + r'\s*(?:负责(?:分发|分派|协调|统筹)|担任负责人|作为负责人)', text) for name in names):
                    leader = identity
                    break
        if leader:
            if self.members[author]['role'] != 'human' or leader not in self.members or self.members[leader]['role'] != 'agent' or not self.members[leader].get('enabled', True):
                raise APIError(400, '负责人必须是启用的 AI，由用户指定')
            self.devices.permit(leader, 'execute')
            participants = list(dict.fromkeys(participants + [leader]))
            targets = [leader]
        target = targets[0] if targets else None
        depth = 0
        if parent_id is not None:
            if not isinstance(parent_id, int) or isinstance(parent_id, bool):
                raise APIError(400, 'reply_to 必须是消息编号')
            parent = db.execute('SELECT * FROM messages WHERE id=?', (parent_id,)).fetchone()
            if not parent:
                raise APIError(400, '被回复的消息不存在')
            depth = parent['depth'] + 1
        if target and self.members[author]['role'] == 'agent':
            if parent_id is None:
                raise APIError(400, 'AI 派发任务时必须附上 reply_to，关联用户任务')
            source_job = db.execute('SELECT * FROM jobs WHERE message_id=? AND target=?', (parent_id, author)).fetchone()
            if not source_job:
                raise APIError(403, '只能从派给自己的任务继续协作')
            ancestor = source_job
            while ancestor['parent_job'] and not ancestor['team_id']:
                ancestor = db.execute('SELECT * FROM jobs WHERE id=?', (ancestor['parent_job'],)).fetchone()
            team = db.execute('SELECT * FROM collaborations WHERE id=?', (ancestor['team_id'],)).fetchone() if ancestor['team_id'] else None
            if team and team['leader'] and team['leader'] != author:
                raise APIError(409, '请通过 finish 把新增任务需求交给负责人，由负责人统一分派')
        if targets and self.members[author]['role'] == 'agent':
            self._adopt_leader(db, source_job, author)
        cursor = db.execute('INSERT INTO messages(author,text,target,reply_to,depth,created,key,targets,source,conversation_id,request_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                            (author, text.strip(), target, parent_id, depth, time.time(), key, json.dumps(targets), source, conversation_id, request_hash))
        for target_id in targets:
            if self.members[target_id]['role'] == 'agent':
                db.execute('INSERT INTO jobs(message_id,target,parent_job) VALUES(?,?,?)', (cursor.lastrowid, target_id, source_job['id'] if self.members[author]['role'] == 'agent' else None))
        if targets and self.members[author]['role'] == 'agent' and source_job['status'] in ('done', 'failed'):
            db.execute("UPDATE jobs SET status='waiting',completed=0 WHERE id=?", (source_job['id'],))
            if source_job['team_id']:
                db.execute("UPDATE collaborations SET status='active' WHERE id=?", (source_job['team_id'],))
        if self.members[author]['role'] == 'human' and targets:
            agents = [identity for identity in participants if self.members[identity]['role'] == 'agent']
            db.execute('INSERT INTO collaborations(id,participants,leader,execution) VALUES(?,?,?,?)', (cursor.lastrowid, json.dumps(agents), leader, json.dumps(execution)))
            db.execute('UPDATE jobs SET team_id=? WHERE message_id=?', (cursor.lastrowid, cursor.lastrowid))
        if execution:
            db.execute('UPDATE messages SET execution=? WHERE id=?', (json.dumps(execution), cursor.lastrowid))
        self.attachments.bind(db, author, conversation_id, cursor.lastrowid, payload.get('attachments', []))
        return self.public_message(db.execute('SELECT * FROM messages WHERE id=?', (cursor.lastrowid,)).fetchone())

    def message(self, author, payload):
        self.devices.permit(author, 'execute')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            result = {'message': self._insert(db, author, payload)}
            jobs = db.execute('SELECT id,target FROM jobs WHERE message_id=? ORDER BY id',(result['message']['id'],)).fetchall()
            result['receipt'] = {'text':'收到' if jobs else '消息已保存', 'task_id':result['message']['id'] if jobs else None,
                'conversation_id':result['message']['conversation_id'], 'jobs':[dict(j) for j in jobs]}
        self.notify()
        return result

    def claim(self, author):
        self.devices.permit(author, 'execute')
        result = self._claim(author)
        if result['job']:
            self._arm_lease(result['job']['id'], result['job']['lease_until'])
            self.notify(observers_only=True)
        return result

    def _claim(self, author):
        if self.paused(author): return {'job': None}
        if not self.members[author].get('enabled', True):
            raise APIError(409, '该成员当前未启用')
        if self.members[author]['role'] != 'agent':
            raise APIError(403, '用户身份不能领取 AI 任务')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            control = db.execute('SELECT paused FROM worker_controls WHERE member=?',(author,)).fetchone()
            if control and control['paused']: return {'job': None}
            row = db.execute('''SELECT * FROM jobs WHERE target=? AND
                (status='pending' OR (status='running' AND lease_until<?)) AND NOT EXISTS (SELECT 1 FROM job_dependencies d LEFT JOIN collaborations c ON c.id=d.team_id WHERE d.job_id=jobs.id AND COALESCE(c.status,'missing') != 'done') ORDER BY id LIMIT 1''',
                (author, time.time())).fetchone()
            if not row:
                return {'job': None}
            lease = secrets.token_urlsafe(24)
            db.execute("UPDATE jobs SET status='running',completed=0,blocked=0,lease=?,lease_until=?,attempts=attempts+1 WHERE id=?",
                       (lease, time.time() + 900, row['id']))
            job = dict(db.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone())
            job['self'] = {key: self.members[author].get(key, '') for key in ('id', 'name', 'machine', 'capabilities')}
            job['message'] = self.public_message(db.execute('SELECT * FROM messages WHERE id=?', (row['message_id'],)).fetchone())
            job['message']['target'] = author
            job['context'] = [self.public_message(r) for r in db.execute('SELECT * FROM (SELECT * FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 20) ORDER BY id',(job['conversation_id'],))]
            ancestor = row
            while ancestor['parent_job'] and not ancestor['team_id']:
                ancestor = db.execute('SELECT * FROM jobs WHERE id=?', (ancestor['parent_job'],)).fetchone()
            if ancestor['team_id']:
                team = db.execute('SELECT * FROM collaborations WHERE id=?', (ancestor['team_id'],)).fetchone()
                job['collaboration'] = dict(team)
                settings = json.loads(team['execution'])
                job['collaboration']['execution'] = settings
                if author.startswith('codex-'):
                    job['execution'] = {**settings.get('*', {}), **settings.get(author, {})}
                job['collaboration']['participants'] = json.loads(team['participants'])
                job['collaboration']['result_targets'] = [team['leader']] if team['leader'] else json.loads(team['participants'])
            return {'job': job}

    def renew(self, author, payload):
        self.devices.permit(author, 'execute')
        if self.members[author]['role'] != 'agent' or not self.members[author].get('enabled', True):
            raise APIError(403, '不能续租任务')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE id=?', (payload.get('job_id'),)).fetchone()
            if not row:
                raise APIError(404, '任务不存在')
            if row['target'] != author:
                raise APIError(403, '不能续租其他成员的任务')
            lease = payload.get('lease')
            if not isinstance(lease, str) or not secrets.compare_digest(row['lease'] or '', lease):
                raise APIError(409, '任务领取凭证已失效')
            if row['status'] != 'running' or row['lease_until'] < time.time():
                raise APIError(409, '任务已过期，不能续租')
            until = time.time() + 900
            db.execute('UPDATE jobs SET lease_until=? WHERE id=?', (until, row['id']))
        self._arm_lease(row['id'], until)
        return {'job_id': row['id'], 'lease_until': until}

    def finish(self, author, payload):
        self.devices.permit(author, 'execute')
        if payload.get('delegates'):
            self.devices.permit(author, 'delegate')
        result = self._finish(author, payload)
        self._arm_lease(payload.get('job_id'), None)
        self.notify()
        return result

    def _finish(self, author, payload):
        envelope = '@@GROUP_RESULT_V1@@'
        if isinstance(payload.get('text'), str) and payload['text'].startswith(envelope):
            try:
                result = json.loads(payload['text'][len(envelope):])
                if not isinstance(result, dict) or set(result) != {'text', 'completed', 'blocked'} or not isinstance(result['text'], str) or not isinstance(result['completed'], bool) or not isinstance(result['blocked'], bool):
                    raise ValueError('Invalid result envelope')
            except (ValueError, TypeError):
                raise APIError(400, '兼容完成消息格式无效')
            payload = dict(payload, **result)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE id=?', (payload.get('job_id'),)).fetchone()
            if not row:
                raise APIError(404, '任务不存在')
            if row['target'] != author:
                raise APIError(403, '不能结束其他成员的任务')
            lease = payload.get('lease')
            if not isinstance(lease, str) or not secrets.compare_digest(row['lease'] or '', lease):
                raise APIError(409, '任务领取凭证已失效')
            if row['reply_id']:
                return {'message': self.public_message(db.execute('SELECT * FROM messages WHERE id=?', (row['reply_id'],)).fetchone())}
            if row['status'] != 'running' or row['lease_until'] < time.time():
                raise APIError(409, '任务已超时，请重新领取')
            if not isinstance(payload.get('text'), str) or not payload['text'].strip() or len(payload['text']) > 16000:
                raise APIError(400, '消息需包含 1–16000 个字符')
            delegates = payload.get('delegates', [])
            if not isinstance(delegates, list) or len(delegates) > 20:
                raise APIError(400, '一次最多邀请 20 位协作者')
            if delegates:
                self._adopt_leader(db, row, author)
                ancestor = row
                while ancestor['parent_job'] and not ancestor['team_id']:
                    ancestor = db.execute('SELECT * FROM jobs WHERE id=?', (ancestor['parent_job'],)).fetchone()
                team = db.execute('SELECT * FROM collaborations WHERE id=?', (ancestor['team_id'],)).fetchone() if ancestor['team_id'] else None
                if team and team['leader'] and team['leader'] != author:
                    for delegate in delegates:
                        if not isinstance(delegate, dict) or delegate.get('target') not in self.members or self.members[delegate['target']]['role'] != 'agent' or not self.members[delegate['target']].get('enabled', True) or not isinstance(delegate.get('text'), str) or not delegate['text'].strip() or len(delegate['text']) > 16000:
                            raise APIError(400, '协作请求格式无效')
                    requests = []
                    for index, delegate in enumerate(delegates):
                        request = self._insert(db, author, {'text': delegate['text'], 'reply_to': row['message_id'], 'broadcast': True, 'key': 'request-' + str(row['id']) + '-' + str(index)})
                        db.execute('UPDATE messages SET target=?,targets=? WHERE id=?', (team['leader'], json.dumps([team['leader']]), request['id']))
                        requests.append({'target': delegate['target'], 'message_id': request['id']})
                    payload = dict(payload, text=payload['text'][:10000] + '\n需要负责人继续分派（按 message_id 读取完整需求）：' + json.dumps(requests, ensure_ascii=False), completed=False)
                    delegates = []
            completed = payload.get('completed', True)
            blocked = payload.get('blocked', False)
            if not isinstance(completed, bool) or not isinstance(blocked, bool):
                raise APIError(400, 'completed 和 blocked 必须为布尔值')
            message = self._insert(db, author, {'text': payload.get('text'), 'reply_to': row['message_id'],
                                                'key': 'job-finish-' + str(row['id']) + ('-attempt-' + str(row['attempts']) if row['attempts'] > 1 else ''), 'broadcast': True})
            db.execute('UPDATE jobs SET status=?,reply_id=?,finished=?,completed=?,blocked=? WHERE id=?',
                       ('waiting' if delegates else 'failed' if payload.get('failed') is True else 'done',
                        message['id'], time.time(), int(completed and not delegates), int(blocked), row['id']))
            result_targets = []
            ancestor = row
            while ancestor['parent_job'] and not ancestor['team_id']:
                ancestor = db.execute('SELECT * FROM jobs WHERE id=?', (ancestor['parent_job'],)).fetchone()
            if ancestor['team_id']:
                team = db.execute('SELECT * FROM collaborations WHERE id=?', (ancestor['team_id'],)).fetchone()
                result_targets = [team['leader']] if team['leader'] else json.loads(team['participants'])
            elif row['parent_job']:
                result_targets = [db.execute('SELECT target FROM jobs WHERE id=?', (row['parent_job'],)).fetchone()[0]]
            if result_targets:
                db.execute('UPDATE messages SET target=?,targets=? WHERE id=?', (result_targets[0], json.dumps(result_targets), message['id']))
                message['target'], message['targets'] = result_targets[0], result_targets
            for index, delegate in enumerate(delegates):
                if not isinstance(delegate, dict) or delegate.get('target') not in self.members or self.members[delegate['target']]['role'] != 'agent':
                    raise APIError(400, '协作者必须是已注册的 AI')
                child = self._insert(db, author, {'text': delegate.get('text'), 'target': delegate['target'],
                      'reply_to': row['message_id'], 'key': 'delegate-' + str(row['id']) + '-' + str(index)})
                db.execute('UPDATE jobs SET parent_job=? WHERE message_id=?', (row['id'], child['id']))
            manual_children = db.execute("SELECT * FROM jobs WHERE parent_job=? AND kind='task'", (row['id'],)).fetchall()
            if not delegates and manual_children:
                db.execute("UPDATE jobs SET status='waiting',completed=0 WHERE id=?", (row['id'],))
                self._schedule_summary(db, manual_children[-1]['id'], author)
            elif not delegates:
                completed_id = row['id']
                current = row
                while current['kind'] == 'summary' and current['parent_job']:
                    completed_id = current['parent_job']
                    db.execute('UPDATE jobs SET status=?,reply_id=?,finished=?,completed=?,blocked=? WHERE id=?',
                        ('failed' if payload.get('failed') is True else 'done', message['id'], time.time(), int(completed), int(blocked), completed_id))
                    current = db.execute('SELECT * FROM jobs WHERE id=?', (completed_id,)).fetchone()
                self._schedule_summary(db, completed_id, author)
                if current['team_id']:
                    self._advance_team(db, current['team_id'])
            return {'message': message}

    def control_worker(self, author, payload):
        member = payload.get('member', author)
        if member not in self.members or self.members[member]['role']!='agent':
            raise APIError(400, '只能控制现有工作成员')
        if author != 'owner' and author != member:
            raise APIError(403, '只能暂停或恢复自己的新任务领取')
        self.devices.permit(author, 'execute')
        paused = payload.get('paused')
        if type(paused) is not bool: raise APIError(400, 'paused 必须为布尔值')
        with self.db() as db:
            db.execute('INSERT INTO worker_controls(member,paused,reason) VALUES(?,?,?) ON CONFLICT(member) DO UPDATE SET paused=excluded.paused,reason=excluded.reason',
                (member,int(paused),'安全维护：暂停新领取' if paused else ''))
        self.notify()
        return {'ok':True,'member':member,'paused':paused}

    def retry_job(self, author, payload):
        self.devices.admin(author)
        reason = payload.get('reason', '')
        if not isinstance(reason,str) or not 1<=len(reason)<=500:
            raise APIError(400, '请说明重试原因')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            job = db.execute('SELECT * FROM jobs WHERE id=?',(payload.get('job_id'),)).fetchone()
            if not job: raise APIError(404, '任务不存在')
            if self.members.get(job['target'],{}).get('state') in ('archived','deleted'):raise APIError(409,'原执行成员已归档或删除，请向新成员重新派单')
            if job['status'] not in ('failed',) and not (job['status']=='done' and job['blocked']):
                raise APIError(409, '只能恢复失败或等待处理障碍的任务；不会重复执行运行中或已完成任务')
            db.execute('INSERT INTO job_recoveries(job_id,actor,previous_status,previous_reply_id,reason,created) VALUES(?,?,?,?,?,?)',
                (job['id'],author,job['status'],job['reply_id'],reason,time.time()))
            db.execute("UPDATE jobs SET status='pending',lease=NULL,lease_until=NULL,reply_id=NULL,finished=NULL,completed=0,blocked=0 WHERE id=?",(job['id'],))
            if job['team_id']: db.execute("UPDATE collaborations SET status='active' WHERE id=?",(job['team_id'],))
        self.notify()
        return {'ok':True,'job_id':job['id'],'state':'pending'}

    def _adopt_leader(self, db, job, author):
        ancestor = job
        while ancestor['parent_job'] and not ancestor['team_id']:
            ancestor = db.execute('SELECT * FROM jobs WHERE id=?', (ancestor['parent_job'],)).fetchone()
        if ancestor['team_id']:
            db.execute('UPDATE collaborations SET leader=? WHERE id=? AND leader IS NULL', (author, ancestor['team_id']))

    @staticmethod
    def compact_context(message, limit=300):
        return {'id': message['id'], 'author': message['author'], 'source': message.get('source', 'legacy'), 'text': message['text'][:limit],
                'truncated': len(message['text']) > limit, 'targets': message.get('targets', [])}

    def _schedule_summary(self, db, completed_id, author):
        completed = db.execute('SELECT * FROM jobs WHERE id=?', (completed_id,)).fetchone()
        if not completed['parent_job']:
            return
        parent = db.execute('SELECT * FROM jobs WHERE id=?', (completed['parent_job'],)).fetchone()
        if not parent or parent['status'] != 'waiting':
            return
        children = db.execute("SELECT * FROM jobs WHERE parent_job=? AND kind='task' ORDER BY id", (parent['id'],)).fetchall()
        if any(child['status'] not in ('done', 'failed') for child in children):
            return
        original = self.public_message(db.execute('SELECT * FROM messages WHERE id=?', (parent['message_id'],)).fetchone())
        replies = [self.public_message(db.execute('SELECT * FROM messages WHERE id=?', (child['reply_id'],)).fetchone()) for child in children]
        text = '协作者本阶段已返回，请核验整体目标是否完成；未完成可继续分派，完成才提交 completed:true。\n' + json.dumps(
            {'original_task': self.compact_context(original, 4000), 'collaborator_replies': [self.compact_context(reply) for reply in replies]}, ensure_ascii=False)
        generation = '-retry-' + hashlib.sha256(json.dumps([(c['id'],c['attempts']) for c in children]).encode()).hexdigest()[:12] if any(c['attempts']>1 for c in children) else ''
        # This is a hub-generated continuation, not a member-authored message.
        cursor = db.execute('INSERT INTO messages(author,text,target,reply_to,depth,created,key) VALUES(?,?,?,?,?,?,?)',
            ('hub', text[:16000], parent['target'], parent['message_id'], original['depth'] + 1,
             time.time(), 'summary-' + str(parent['id']) + '-' + str(children[-1]['id']) + generation))
        db.execute("INSERT INTO jobs(message_id,target,parent_job,kind) VALUES(?,?,?,'summary')",
                   (cursor.lastrowid, parent['target'], parent['id']))

    def _advance_team(self, db, team_id):
        team = db.execute('SELECT * FROM collaborations WHERE id=?', (team_id,)).fetchone()
        if not team or team['status'] != 'active':
            return
        jobs = db.execute('SELECT * FROM jobs WHERE team_id=? ORDER BY id', (team_id,)).fetchall()
        last_message = jobs[-1]['message_id']
        current = [job for job in jobs if job['message_id'] == last_message]
        if any(job['status'] not in ('done', 'failed') for job in current):
            return
        if any(job['status'] == 'failed' for job in current):
            db.execute("UPDATE collaborations SET status='failed' WHERE id=?", (team_id,))
            return
        if any(job['blocked'] for job in current):
            db.execute("UPDATE collaborations SET status='blocked' WHERE id=?", (team_id,))
            return
        if all(job['completed'] and job['status'] == 'done' for job in current):
            db.execute("UPDATE collaborations SET status='done' WHERE id=?", (team_id,))
            return
        original = self.public_message(db.execute('SELECT * FROM messages WHERE id=?', (team_id,)).fetchone())
        replies = [self.public_message(db.execute('SELECT * FROM messages WHERE id=?', (job['reply_id'],)).fetchone()) for job in current]
        participants = json.loads(team['participants'])
        targets = [team['leader']] if team['leader'] else participants
        round_number = team['round'] + 1
        text = '继续推进原任务。以下是本轮全部参与者的结果；解决剩余问题，核验完成后才标记 completed:true。需要用户信息或授权时标记 blocked:true 并说明。truncated:true 的消息需按 id 读取完整群历史后继续执行。\n' + json.dumps({'original_task': self.compact_context(original, 4000), 'participants': participants, 'leader': team['leader'], 'round': round_number, 'results': [self.compact_context(reply) for reply in replies]}, ensure_ascii=False)
        cursor = db.execute('INSERT INTO messages(author,text,target,reply_to,depth,created,key,targets) VALUES(?,?,?,?,?,?,?,?)',
            ('hub', text[:16000], targets[0], team_id, original['depth'] + 1, time.time(), 'team-' + str(team_id) + '-round-' + str(round_number), json.dumps(targets)))
        for identity in targets:
            db.execute("INSERT INTO jobs(message_id,target,team_id,kind) VALUES(?,?,?,'continuation')", (cursor.lastrowid, identity, team_id))
        db.execute('UPDATE collaborations SET round=? WHERE id=?', (round_number, team_id))


def make_server(room, host, port):
    public_origin = os.environ.get('AICOLLABHUB_PUBLIC_ORIGIN')
    if public_origin:
        from mcp_http import MCPBridge
        mcp_bridge = MCPBridge(room, public_origin)
    else:
        mcp_bridge = None
    pair_lock = threading.Lock()
    pair_limits = [0,0]
    class Handler(BaseHTTPRequestHandler):
        rbufsize = 0
        protocol_version = "HTTP/1.1"

        def notifications(self):
            reader = None
            upgraded = False
            try:
                self.connection.settimeout(10)
                if self.headers.get('Upgrade', '').lower() != 'websocket' or 'upgrade' not in self.headers.get('Connection', '').lower() or self.headers.get('Sec-WebSocket-Version') != '13':
                    raise APIError(400, '需要 WebSocket 通知连接')
                accepted = accept_key(self.headers.get('Sec-WebSocket-Key', ''))
                self.send_response(101)
                self.send_header('Upgrade', 'websocket')
                self.send_header('Connection', 'Upgrade')
                self.send_header('Sec-WebSocket-Accept', accepted)
                self.end_headers()
                upgraded = True
                opcode, data = read_frame(self.rfile, masked=True)
                if opcode != 1:
                    raise APIError(401, '通知连接需要身份凭证')
                payload = json.loads(data.decode())
                if not isinstance(payload, dict) or not isinstance(payload.get('token'), str) or len(payload['token']) > 256:
                    raise APIError(401, '通知连接需要有效身份凭证')
                author, readonly = room.authenticate(payload['token'])
                if not room.members[author].get('enabled', True):
                    raise APIError(409, '该成员当前未启用')
                room.devices.permit(author, 'read')
                reader = room.subscribe(author)
                self.connection.settimeout(None)
                def send_state():
                    room.authenticate(payload['token'])
                    room.devices.permit(author, 'read')
                    state = {'type': 'state', 'ready': False if readonly else room.has_job(author)}
                    self.connection.sendall(frame(json.dumps(state)))
                send_state()
                # No queue timer. Socketpair signals committed room changes; ping only preserves transport.
                while True:
                    readable, _, _ = select.select([self.connection, reader], [], [], 30)
                    if not readable:
                        self.connection.sendall(frame(b'', opcode=9))
                        continue
                    if reader in readable:
                        signal = reader.recv(4096)
                        if not signal:
                            break
                        send_state()
                    if self.connection in readable:
                        opcode, data = read_frame(self.rfile, masked=True)
                        if opcode == 8:
                            self.connection.sendall(frame(b'', opcode=8))
                            break
                        if opcode == 9:
                            self.connection.sendall(frame(data, opcode=10))
                        elif opcode != 10:
                            raise APIError(400, '通知通道不接收任务，请使用已认证的 HTTP 接口')
            except (APIError, ValueError, TypeError) as error:
                if upgraded:
                    try:
                        self.connection.sendall(frame(json.dumps({'type': 'error', 'error': str(error)})))
                        self.connection.sendall(frame(b'', opcode=8))
                    except OSError:
                        pass
                else:
                    self.respond(error.status if isinstance(error, APIError) else 400, {'error': str(error)})
            except (OSError, EOFError):
                pass
            finally:
                if reader is not None:
                    room.unsubscribe(reader)
                self.close_connection = True
        def log_message(self, *args):
            pass  # Never log authorization headers or user message content.

        def respond(self, status, data, content_type='application/json; charset=utf-8', headers=None):
            if urlsplit(self.path).path == '/mcp':
                print('MCP HTTP', self.command, status, 'version='+self.headers.get('MCP-Protocol-Version','(absent)')[:32], 'accept='+self.headers.get('Accept','(absent)')[:100], flush=True)
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header('Connection', 'close')
            self.close_connection = True
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            # Same-origin form submissions need a non-opaque Origin in Safari.
            # Still suppress referrers when leaving this service for ChatGPT.
            if not any(key.lower() == 'referrer-policy' for key in (headers or {})):
                self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)

        def handle_api(self, post=False):
            self.connection.settimeout(15)
            try:
                parsed = urlsplit(self.path)
                if mcp_bridge and mcp_bridge.handle(self, parsed.path, post):
                    return
                if parsed.path == '/api/notifications' and not post:
                    return self.notifications()
                if parsed.path == '/' and not post:
                    return self.respond(200, Path(__file__).with_name('index.html').read_bytes(), 'text/html; charset=utf-8')
                if parsed.path in ('/guide', '/client.py') and not post:
                    filename = 'MUSE.md' if parsed.path == '/guide' else 'client.py'
                    return self.respond(200, Path(__file__).with_name(filename).read_bytes(), 'text/plain; charset=utf-8')
                if parsed.path == '/health' and not post:
                    return self.respond(200, {'ok': True})
                public_files = {'/devices.js':'devices.js', '/appearance.css':'appearance.css',
                    '/attachments.js':'attachments.js', '/attachments.css':'attachments.css',
                    '/appearance.js':'appearance.js', '/downloads/pair_device.py':'pair_device.py',
                    '/downloads/worker.py':'worker.py', '/downloads/client.py':'client.py',
                    '/downloads/notifications.py':'notifications.py'}
                if parsed.path in public_files and not post:
                    filename = public_files[parsed.path]
                    mime = 'text/javascript' if filename.endswith('.js') else 'text/css' if filename.endswith('.css') else 'text/plain'
                    return self.respond(200, Path(__file__).with_name(filename).read_bytes(), mime+'; charset=utf-8')
                if parsed.path == '/downloads/manifest.json' and not post:
                    names = ('worker.py','client.py','notifications.py','pair_device.py')
                    return self.respond(200, {'protocol':1,'files':{n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in names}})
                if parsed.path.startswith('/api/devices'):
                    if post:
                        origin = self.headers.get('Origin')
                        expected = public_origin.rstrip('/') if public_origin else None
                        same_origin = (origin == expected) if expected else (origin and urlsplit(origin).scheme in ('http','https') and urlsplit(origin).netloc == self.headers.get('Host','') and urlsplit(origin).path == '')
                        if origin and not same_origin:
                            raise APIError(403, '管理请求必须来自本站')
                        if self.headers.get('Content-Type','').split(';')[0] != 'application/json':
                            raise APIError(415, '请使用 JSON 请求')
                        length = int(self.headers.get('Content-Length','0'))
                        if not 0 < length <= 16000: raise APIError(413, '请求大小无效')
                        payload = json.loads(self.rfile.read(length))
                        if not isinstance(payload,dict): raise APIError(400, '请求必须为对象')
                    if parsed.path == '/api/devices/pair' and post:
                        with pair_lock:
                            now = time.monotonic()
                            if now-pair_limits[0] > 60: pair_limits[:] = [now,0]
                            pair_limits[1] += 1
                            if pair_limits[1]>120: raise APIError(429, '尝试过于频繁，请稍后重试')
                        return self.respond(200, room.devices.pair(payload))
                    auth = self.headers.get('Authorization', '')
                    author, readonly = room.authenticate(auth[7:] if auth.startswith('Bearer ') else '')
                    if readonly: raise APIError(403, '只读凭证不能管理设备')
                    room.devices.admin(author)
                    if parsed.path == '/api/devices' and not post:
                        result = room.devices.listing(author)
                    elif parsed.path == '/api/devices/invite' and post:
                        result = room.devices.invite(author,payload)
                    elif parsed.path == '/api/devices/cloud-credential' and post:
                        result = room.devices.cloud_credential(author,payload)
                    elif parsed.path == '/api/devices/bundle' and post:
                        from onboarding import device_bundle
                        bundle = device_bundle(room, payload, public_origin, self.headers.get('Host',''), APIError)
                        return self.respond(200, bundle, 'application/octet-stream', {'Content-Disposition':'attachment; filename=ai-group-connect.pyz'})
                    elif parsed.path == '/api/devices/action' and post:
                        result = room.devices.revoke(author,payload)
                    else: raise APIError(404, '接口不存在')
                    return self.respond(200,result)
                auth = self.headers.get('Authorization', '')
                author, readonly = room.authenticate(auth[7:] if auth.startswith('Bearer ') else '')
                room.devices.permit(author, 'read')
                if parsed.path == '/api/attachments' or parsed.path.startswith('/api/attachments/'):
                    return room.attachments.handle(self, parsed, post, author, readonly)
                if post:
                    if readonly:
                        raise APIError(403, '此凭证只允许读取，不能发消息、领取或完成任务')
                    length = int(self.headers.get('Content-Length', '0'))
                    if length < 0 or length > 100000:
                        raise APIError(413, '请求过大')
                    payload = json.loads(self.rfile.read(length) or b'{}')
                    if not isinstance(payload, dict):
                        raise APIError(400, '请求体必须为 JSON 对象')
                    if parsed.path == '/api/messages':
                        result = room.message(author, payload)
                    elif parsed.path == '/api/conversations':
                        result = room.save_conversation(author, payload)
                    elif parsed.path == '/api/workers/control':
                        result = room.control_worker(author, payload)
                    elif parsed.path == '/api/jobs/retry':
                        result = room.retry_job(author, payload)
                    elif parsed.path == '/api/jobs/claim':
                        result = room.claim(author)
                    elif parsed.path == '/api/jobs/progress':
                        result = room.progress(author, payload)
                    elif parsed.path == '/api/jobs/renew':
                        result = room.renew(author, payload)
                    elif parsed.path == '/api/jobs/finish':
                        result = room.finish(author, payload)
                    elif parsed.path == '/api/presence':
                        result = room.ping(author, payload)
                    else:
                        raise APIError(404, '接口不存在')
                elif parsed.path == '/api/members':
                    result = {'me': author, 'readonly': readonly, 'members': room.member_list()}
                elif parsed.path == '/api/messages':
                    query = parse_qs(parsed.query)
                    after = max(0, int(query.get('after', ['0'])[0]))
                    limit = max(1, min(200, int(query.get('limit', ['100'])[0])))
                    conversation_id = query.get('conversation_id',['main'])[0]
                    before = max(0,int(query.get('before',['0'])[0]))
                    rows = room.messages(after,limit+1,conversation_id,before)
                    more = len(rows)>limit
                    rows = rows[:limit] if after else rows[-limit:]
                    result = {'messages':rows,'has_more':more,'conversation_id':conversation_id}
                    with room.db() as db:
                        result['conversation'] = room.public_conversation(db.execute('SELECT * FROM conversations WHERE id=?',(conversation_id,)).fetchone())
                elif parsed.path == '/api/jobs':
                    query = parse_qs(parsed.query)
                    limit = max(1,min(100,int(query.get('limit',['100'])[0])))
                    rows = room.jobs(query.get('conversation_id',['main'])[0],max(0,int(query.get('before',['0'])[0])),limit+1)
                    result = {'jobs':rows[:limit],'has_more':len(rows)>limit}
                elif parsed.path == '/api/conversations':
                    query = parse_qs(parsed.query)
                    result = room.conversations(query.get('q',[''])[0][:100], max(0,int(query.get('offset',['0'])[0])))
                else:
                    raise APIError(404, '接口不存在')
                self.respond(200, result)
            except APIError as error:
                self.respond(error.status, {'error': error.message})
            except (ValueError, TypeError, OverflowError):
                self.respond(400, {'error': '请求参数格式无效'})
            except sqlite3.Error:
                self.respond(503, {'error': '数据库暂不可用，请重试'})

        def do_HEAD(self):
            self.handle_api()

        def do_GET(self):
            self.handle_api()

        def do_POST(self):
            self.handle_api(True)

    class BoundedServer(ThreadingHTTPServer):
        daemon_threads = True
        request_queue_size = 16
        def __init__(self, *args):
            self.slots = threading.BoundedSemaphore(36)
            super().__init__(*args)

        def server_close(self):
            room.close()
            super().server_close()

        def process_request(self, request, client_address):
            if not self.slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            try:
                super().process_request(request, client_address)
            except BaseException:
                self.slots.release()
                raise

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self.slots.release()

    return BoundedServer((host, port), Handler)


def initialize(directory, codex_count=0, muse_count=0, roster=None):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (directory / 'members.json').exists():
        raise SystemExit('已经初始化，拒绝覆盖现有成员凭证')
    members, enrollments = [], {}
    identities = roster or [{'id': 'owner', 'name': '你', 'role': 'human'}]
    if not roster:
        identities += [{'id': 'codex-' + str(i), 'name': 'Codex ' + str(i), 'role': 'agent'} for i in range(1, codex_count + 1)]
        identities += [{'id': 'muse-' + str(i), 'name': 'Muse ' + str(i), 'role': 'agent'} for i in range(1, muse_count + 1)]
    if len({m['id'] for m in identities}) != len(identities) or any(m['role'] not in ('human', 'agent') for m in identities):
        raise SystemExit('成员 ID 必须唯一，角色必须为 human 或 agent')
    for member in identities:
        if member['role']=='agent' and 'permissions' not in member:
            member=dict(member,permissions=dict(read=True,execute=False,delegate=False))
        name = member['id']
        token = secrets.token_urlsafe(32)
        members.append(dict(member, token_hash=hashlib.sha256(token.encode()).hexdigest()))
        enrollments[name] = token
        if name.startswith('muse-'):
            read_token = secrets.token_urlsafe(32)
            members[-1]['read_token_hash'] = hashlib.sha256(read_token.encode()).hexdigest()
            enrollments[name + '-read'] = read_token
    config = {'members': members}
    for filename, data in [('members.json', config), ('enrollment.json', enrollments)]:
        path = directory / filename
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(data, output, ensure_ascii=False, indent=2)
    print('已创建成员身份；凭证仅保存在 ' + str(directory / 'enrollment.json'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['init', 'serve'])
    parser.add_argument('--data-dir', default='data')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--codex-count', type=int, default=0)
    parser.add_argument('--muse-count', type=int, default=0)
    parser.add_argument('--roster', help='JSON 成员列表')
    args = parser.parse_args()
    directory = Path(args.data_dir).resolve()
    os.umask(0o077)
    if args.command == 'init':
        if not 0 <= args.codex_count <= 100 or not 0 <= args.muse_count <= 100:
            parser.error('成员数量需为 0–100；默认只初始化管理员')
        roster = json.loads(Path(args.roster).read_text()) if args.roster else None
        initialize(directory, args.codex_count, args.muse_count, roster)
        return
    config = json.loads((directory / 'members.json').read_text())
    room = Room(directory / 'room.db', config)
    http = make_server(room, args.host, args.port)
    print('群聊服务已启动，端口 ' + str(args.port), flush=True)
    try:
        http.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        http.server_close()


if __name__ == '__main__':
    main()
