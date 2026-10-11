"""Device enrollment. Additive SQLite storage; no plaintext secrets at rest."""
import hashlib
import json
import secrets
import time
import threading
import unicodedata

TYPES = ('codex-linux', 'codex-macos', 'codex-windows', 'muse')
PERMISSIONS = ('read', 'execute', 'delegate')

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

class DeviceRegistry:
    def __init__(self, room, error):
        self.room, self.error = room, error
        self.refresh_lock = threading.Lock()
        self.base_members = dict(room.members)
        with room.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS devices (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT UNIQUE NOT NULL,
              type TEXT NOT NULL, machine TEXT NOT NULL, tags TEXT NOT NULL,
              permissions TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL,
              credential_hash TEXT, generation INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS device_invitations (
              id TEXT PRIMARY KEY, device_id TEXT NOT NULL, secret_hash TEXT NOT NULL,
              expires REAL NOT NULL, consumed REAL, revoked REAL);
            CREATE TABLE IF NOT EXISTS device_requests (
              key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, device_id TEXT NOT NULL,
              invitation_id TEXT);
            CREATE TABLE IF NOT EXISTS device_audit (
              id INTEGER PRIMARY KEY, actor TEXT NOT NULL, device_id TEXT NOT NULL,
              action TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS agent_retirements (
              member TEXT PRIMARY KEY, state TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS agent_settings (
              member TEXT PRIMARY KEY, enabled INTEGER NOT NULL,
              permissions TEXT NOT NULL, generation INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS agent_credentials (
              member TEXT PRIMARY KEY, credential_hash TEXT NOT NULL, updated REAL NOT NULL);
            ''')
            # Keep historical labels/IDs while releasing the internal unique name slot.
            # Control characters cannot be submitted as a new name.
            db.execute("UPDATE devices SET name_key=char(0)||'retired:'||id WHERE state IN ('archived','deleted')")
        self.refresh()

    def refresh(self):
        with self.refresh_lock:
            self._refresh()

    def _refresh(self):
        with self.room.db() as db:
            rows = list(db.execute('SELECT * FROM devices'))
            retired = {r['member']:r['state'] for r in db.execute('SELECT * FROM agent_retirements')}
            settings = {r['member']:dict(r) for r in db.execute('SELECT * FROM agent_settings')}
            credentials = {r['member']:r['credential_hash'] for r in db.execute('SELECT * FROM agent_credentials')}
        members = {k:dict(v, enabled=False, state=retired[k], token_hash='', read_token_hash='') if k in retired else v
                   for k,v in self.base_members.items()}
        for identity, setting in settings.items():
            if identity in members and identity not in retired:
                members[identity] = dict(members[identity], enabled=bool(setting['enabled']),
                    permissions=json.loads(setting['permissions']), state='active' if setting['enabled'] else 'disabled')
        for identity, credential in credentials.items():
            if identity in members and identity not in retired:
                members[identity] = dict(members[identity], token_hash=credential, read_token_hash='')
        for d in rows:
            members[d['id']] = dict(id=d['id'], name=d['name'], role='agent',
                machine=d['machine'], device=True, device_type=d['type'],
                enabled=d['state']=='active', state=d['state'], token_hash='', permissions=json.loads(d['permissions']))
        self.room.members = members

    def fail(self, status, message):
        raise self.error(status, message)

    def admin(self, author):
        if author != 'owner' or not self.room.members.get(author, {}).get('enabled', True):
            self.fail(403, '只有管理员可以管理设备')

    def get(self, identity):
        with self.room.db() as db:
            row = db.execute('SELECT * FROM devices WHERE id=?', (identity,)).fetchone()
        return row

    def permit(self, author, capability):
        row = self.get(author)
        if row and (row['state'] != 'active' or not json.loads(row['permissions']).get(capability, False)):
            self.fail(403, '此设备未获授权：' + capability)
        if not row:
            member = self.room.members.get(author, {})
            # Old configuration members keep their existing disabled-member contract.
            # Authentication and task routing check enabled; only explicit settings
            # add capability restrictions to legacy identities.
            if member.get('role') == 'agent' and 'permissions' in member and not member['permissions'].get(capability, False):
                self.fail(403, '此成员未获授权：' + capability)

    @staticmethod
    def legacy_permissions(member):
        return member.get('permissions', dict(read=True, execute=member.get('enabled', True), delegate=member.get('enabled', True)))

    def authenticate(self, token):
        with self.room.db() as db:
            row = db.execute("SELECT id FROM devices WHERE credential_hash=? AND state='active'", (digest(token),)).fetchone()
        return row['id'] if row else None

    @staticmethod
    def audit(db, actor, identity, action):
        db.execute('INSERT INTO device_audit(actor,device_id,action,created) VALUES(?,?,?,?)',
                   (actor, identity, action, time.time()))

    def public(self, row, presence=None, invitation=None):
        d = {k:row[k] for k in ('id','name','type','machine','state','created','generation')}
        d['tags'], d['permissions'], d['role'] = json.loads(row['tags']), json.loads(row['permissions']), 'agent'
        d['last_seen'] = presence['seen'] if presence else None
        d['status'] = row['state']
        if row['state'] == 'active':
            d['status'] = 'connected' if presence and time.time()-presence['seen'] < (150 if row['type']=='muse' else 60) else ('offline' if presence else 'waiting')
        if row['state'] == 'invited' and invitation and invitation['expires'] < time.time():
            d['status'] = 'expired'
        d['invitation_expires'] = invitation['expires'] if invitation and not invitation['revoked'] and not invitation['consumed'] else None
        return d

    def listing(self, author):
        self.admin(author)
        with self.room.db() as db:
            result=[]
            for d in db.execute("SELECT * FROM devices WHERE state!='deleted' ORDER BY created DESC"):
                p = db.execute('SELECT * FROM presence WHERE member=?', (d['id'],)).fetchone()
                i = db.execute('SELECT * FROM device_invitations WHERE device_id=? ORDER BY expires DESC LIMIT 1', (d['id'],)).fetchone()
                result.append(self.public(d,p,i))
            retired={r['member']:r['state'] for r in db.execute('SELECT * FROM agent_retirements')}
            for member in self.base_members.values():
                if member['role']!='agent' or retired.get(member['id'])=='deleted':continue
                setting=db.execute('SELECT * FROM agent_settings WHERE member=?',(member['id'],)).fetchone()
                enabled=bool(setting['enabled']) if setting else member.get('enabled',True)
                perms=json.loads(setting['permissions']) if setting else self.legacy_permissions(member)
                state=retired.get(member['id'], 'active' if enabled else 'disabled')
                presence=db.execute('SELECT seen FROM presence WHERE member=?',(member['id'],)).fetchone()
                seen=presence['seen'] if presence else None
                result.append(dict(id=member['id'],name=member['name'],type='muse' if member['id'].startswith('muse-') else 'codex-linux',
                    machine=member.get('machine',''),tags=[],legacy=True,state=state,
                    status=('connected' if seen and time.time()-seen<150 else 'offline') if state=='active' else state,
                    last_seen=seen,permissions=perms,generation=setting['generation'] if setting else 0))
        return {'devices':result, 'types':list(TYPES)}

    def fields(self, payload):
        def string(key, maxlen, default=''):
            val=payload.get(key,default)
            if not isinstance(val,str) or len(val)>maxlen or any(ord(c)<32 for c in val):
                self.fail(400, '字段格式无效：'+key)
            return val.strip()
        name=string('name',60)
        if not name: self.fail(400,'请填写设备名称')
        kind=string('type',30)
        if kind not in TYPES: self.fail(400,'不支持的设备类型')
        tags=payload.get('tags',[])
        if not isinstance(tags,list) or len(tags)>12 or any(not isinstance(t,str) or not 1<=len(t)<=32 for t in tags):
            self.fail(400,'标签最多12项，每项1–32字')
        perms=payload.get('permissions',{'read':True,'execute':False,'delegate':False})
        if not isinstance(perms,dict) or set(perms)!=set(PERMISSIONS) or any(type(v)!=bool for v in perms.values()):
            self.fail(400,'权限必须分别明确为布尔值')
        if (perms['execute'] and not perms['read']) or (perms['delegate'] and not perms['execute']):
            self.fail(400,'执行需要读取权限，委派需要执行权限')
        return dict(name=name, name_key=unicodedata.normalize('NFKC',name).casefold(),type=kind,
                    machine=string('machine',160),tags=tags,permissions=perms)

    def request_key(self, payload):
        key=payload.get('key')
        if not isinstance(key,str) or not 8<=len(key)<=100: self.fail(400,'操作需要8–100字的幂等 key')
        return key

    def invite(self, author, payload):
        self.admin(author)
        key=self.request_key(payload)
        action=payload.get('action','create')
        if action not in ('create','reinvite'): self.fail(400,'邀请操作无效')
        fields=self.fields(payload) if action=='create' else None
        request_hash=digest(json.dumps(payload,sort_keys=True,ensure_ascii=False))
        code=secrets.token_urlsafe(32); invitation_id=secrets.token_urlsafe(18); now=time.time()
        with self.room.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM device_requests WHERE key=?',(key,)).fetchone()
            if old:
                if old['request_hash']!=request_hash:self.fail(409,'相同 key 对应不同请求')
                return {'device_id':old['device_id'],'invitation_id':old['invitation_id'],
                        'replayed':True,'secret_available':False,'notice':'此前邀请已创建；配对码只显示一次。若未保存，请重发邀请。'}
            if action=='create':
                retired={r['member'] for r in db.execute('SELECT member FROM agent_retirements')}
                if any(unicodedata.normalize('NFKC',m['name']).casefold()==fields['name_key'] for m in self.base_members.values() if m['id'] not in retired):
                    self.fail(409,'设备名称已存在，请换一个名称')
                if db.execute('SELECT 1 FROM devices WHERE name_key=?',(fields['name_key'],)).fetchone():self.fail(409,'设备名称已存在')
                if db.execute("SELECT count(*) FROM devices WHERE state NOT IN ('archived','deleted')").fetchone()[0]>=200:self.fail(409,'设备数量已达上限200')
                identity=('codex-' if fields['type'].startswith('codex-') else 'muse-')+'device-'+secrets.token_hex(8)
                db.execute('INSERT INTO devices(id,name,name_key,type,machine,tags,permissions,state,created) VALUES(?,?,?,?,?,?,?,?,?)',
                    (identity,fields['name'],fields['name_key'],fields['type'],fields['machine'],json.dumps(fields['tags']),json.dumps(fields['permissions']),'invited',now))
            else:
                identity=payload.get('device_id')
                d=db.execute('SELECT * FROM devices WHERE id=?',(identity,)).fetchone()
                if not d:self.fail(404,'设备不存在')
                if d['state'] in ('archived','deleted'):self.fail(409,'成员已归档或删除，请创建新成员')
                db.execute("UPDATE devices SET state='invited',credential_hash=NULL,generation=generation+1 WHERE id=?",(identity,))
                db.execute('DELETE FROM presence WHERE member=?',(identity,))
            db.execute('UPDATE device_invitations SET revoked=? WHERE device_id=? AND consumed IS NULL AND revoked IS NULL',(now,identity))
            db.execute('INSERT INTO device_invitations(id,device_id,secret_hash,expires) VALUES(?,?,?,?)',(invitation_id,identity,digest(code),now+600))
            db.execute('INSERT INTO device_requests VALUES(?,?,?,?)',(key,request_hash,identity,invitation_id))
            self.audit(db,author,identity,action)
        self.refresh();self.room.notify()
        return {'device_id':identity,'invitation_id':invitation_id,'pairing_code':code,'expires':now+600,'secret_available':True}

    def pair(self, payload):
        code=payload.get('pairing_code'); identity=payload.get('device_id'); invitation=payload.get('invitation_id')
        if not isinstance(code,str) or not 20<=len(code)<=100: self.fail(401,'配对凭证无效或已过期')
        token=secrets.token_urlsafe(40); now=time.time()
        with self.room.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM device_invitations WHERE id=? AND device_id=?',(invitation,identity)).fetchone()
            if not row or not secrets.compare_digest(row['secret_hash'],digest(code)) or row['expires']<=now or row['revoked'] or row['consumed']:
                self.fail(401,'配对凭证无效、已过期、已使用或已撤销')
            d=db.execute('SELECT * FROM devices WHERE id=?',(identity,)).fetchone()
            if not d or d['state']!='invited' or d['type']!=payload.get('type') or payload.get('protocol')!=1:
                self.fail(409,'设备类型或接入版本不匹配，请重新下载配置')
            db.execute('UPDATE device_invitations SET consumed=? WHERE id=?',(now,invitation))
            db.execute("UPDATE devices SET credential_hash=?,state='active',generation=generation+1 WHERE id=?",(digest(token),identity))
            self.audit(db,identity,identity,'paired')
        self.refresh();self.room.notify()
        return {'member_id':identity,'token':token,'permissions':json.loads(d['permissions']),'protocol':1}

    def cloud_credential(self, author, payload):
        """Owner explicitly transfers a NEW Muse credential into a cloud vault.

        No old secret retrieval. No plaintext at rest or on an idempotent replay.
        The display window expires independently of the member credential.
        """
        self.admin(author)
        identity, expected = payload.get('device_id'), payload.get('generation')
        if not isinstance(identity, str) or not identity:self.fail(400,'请选择 Muse 成员')
        if payload.get('confirm') is not True:self.fail(400,'生成连接密钥需要确认，旧密钥及邀请会失效')
        if type(expected) != int or expected < 0:self.fail(400,'请刷新成员列表后操作')
        key=self.request_key(payload)
        fingerprint=digest(json.dumps({'operation':'cloud-credential','payload':payload},sort_keys=True,ensure_ascii=False))
        now=time.time(); token=secrets.token_urlsafe(40)
        with self.room.db() as db:
            db.execute('BEGIN IMMEDIATE')
            d=db.execute('SELECT * FROM devices WHERE id=?',(identity,)).fetchone()
            legacy = None
            if not d:
                legacy = self.base_members.get(identity)
                if not legacy or legacy['role'] != 'agent' or not identity.startswith('muse-'):
                    self.fail(400,'此入口仅用于 Muse 云端成员')
                retired = db.execute('SELECT state FROM agent_retirements WHERE member=?',(identity,)).fetchone()
                setting = db.execute('SELECT * FROM agent_settings WHERE member=?',(identity,)).fetchone()
                enabled = bool(setting['enabled']) if setting else legacy.get('enabled',True)
                permissions = json.loads(setting['permissions']) if setting else self.legacy_permissions(legacy)
                d = dict(type='muse',state=retired['state'] if retired else ('active' if enabled else 'disabled'),
                         generation=setting['generation'] if setting else 0,permissions=json.dumps(permissions))
            if d['type']!='muse':self.fail(400,'此入口仅用于 Muse 云端成员')
            if d['state']=='disabled':self.fail(409,'请先启用该成员，再生成连接密钥')
            if d['state'] in ('archived','deleted'):self.fail(409,'已归档或删除的身份不能恢复，请添加新成员')
            previous=db.execute('SELECT * FROM device_requests WHERE key=?',(key,)).fetchone()
            if previous:
                if previous['request_hash']!=fingerprint:self.fail(409,'相同 key 对应不同请求')
                return dict(member_id=identity,permissions=json.loads(d['permissions']),protocol=1,
                    secret_available=False,replayed=True,notice='连接密钥此前已生成，不能再次取回。若未保存，请明确重新生成新密钥。')
            if d['generation']!=expected:self.fail(409,'成员设置已变化，请刷新后再生成密钥')
            if not json.loads(d['permissions'])['read']:self.fail(409,'请先在修改权限中开启读取权限')
            if db.execute("SELECT 1 FROM jobs WHERE target=? AND status='running' AND lease_until>? LIMIT 1",(identity,now)).fetchone():
                self.fail(409,'成员有运行中任务，请结束后再更换连接密钥')
            if legacy:
                db.execute('INSERT INTO agent_credentials VALUES(?,?,?) ON CONFLICT(member) DO UPDATE SET credential_hash=excluded.credential_hash,updated=excluded.updated',
                           (identity,digest(token),now))
                db.execute('INSERT INTO agent_settings VALUES(?,?,?,?) ON CONFLICT(member) DO UPDATE SET generation=excluded.generation',
                           (identity,1,d['permissions'],d['generation']+1))
            else:
                db.execute("UPDATE devices SET credential_hash=?,state='active',generation=generation+1 WHERE id=?",(digest(token),identity))
            db.execute('UPDATE device_invitations SET revoked=? WHERE device_id=? AND revoked IS NULL',(now,identity))
            db.execute('DELETE FROM presence WHERE member=?',(identity,))
            db.execute('INSERT INTO device_requests VALUES(?,?,?,NULL)',(key,fingerprint,identity))
            self.audit(db,author,identity,'cloud_credential')
        self.refresh();self.room.notify()
        return dict(member_id=identity,token=token,permissions=json.loads(d['permissions']),protocol=1,
            secret_available=True,display_until=now+300,
            notice='这是成员连接密钥（Bearer token），不是配对码。只显示本次，复制到 Muse 的安全凭证入口；不要发进聊天。')

    def configure(self, author, payload):
        self.admin(author)
        identity, action = payload.get('device_id'), payload.get('action')
        expected = payload.get('generation')
        if not isinstance(identity, str) or not identity:self.fail(400,'请选择成员')
        if type(expected) != int or expected < 0:self.fail(400,'请刷新成员列表后再操作')
        perms = payload.get('permissions')
        if action == 'permissions':
            if not isinstance(perms,dict) or set(perms)!=set(PERMISSIONS) or any(type(v)!=bool for v in perms.values()):
                self.fail(400,'权限必须分别明确为布尔值')
            if (perms['execute'] and not perms['read']) or (perms['delegate'] and not perms['execute']):
                self.fail(400,'执行需要读取权限，委派需要执行权限')
        with self.room.db() as db:
            db.execute('BEGIN IMMEDIATE')
            d=db.execute('SELECT * FROM devices WHERE id=?',(identity,)).fetchone()
            legacy=self.base_members.get(identity)
            if not d and (not legacy or legacy['role']!='agent'):self.fail(403,'只能修改 Agent，不能修改管理员')
            retired=db.execute('SELECT 1 FROM agent_retirements WHERE member=?',(identity,)).fetchone()
            if retired or (d and d['state'] in ('archived','deleted')):
                self.fail(409,'已归档或删除的成员不能恢复，请创建新成员')
            setting=db.execute('SELECT * FROM agent_settings WHERE member=?',(identity,)).fetchone()
            generation=d['generation'] if d else (setting['generation'] if setting else 0)
            current=json.loads(d['permissions'] if d else setting['permissions']) if d or setting else self.legacy_permissions(legacy)
            enabled=d['state']=='active' if d else bool(setting['enabled']) if setting else legacy.get('enabled',True)
            same=(current==perms) if action=='permissions' else enabled
            if expected != generation:
                if expected+1 == generation and same:return {'ok':True,'replayed':True}
                self.fail(409,'成员设置已变化，请刷新列表后重新修改')
            if action=='enable' and d and not enabled:self.fail(409,'请重新生成邀请并配对；旧凭证不能恢复')
            if action=='permissions' and any(current[k] and not perms[k] for k in PERMISSIONS):
                running=db.execute("SELECT 1 FROM jobs WHERE target=? AND status='running' AND lease_until>? LIMIT 1",(identity,time.time())).fetchone()
                if running:self.fail(409,'成员有运行中任务，请任务完成后再收回权限')
            if same:return {'ok':True,'replayed':True}
            if action=='enable':enabled=True
            else:current=perms
            if d:
                db.execute('UPDATE devices SET permissions=?,generation=generation+1 WHERE id=?',(json.dumps(current),identity))
            else:
                db.execute('INSERT INTO agent_settings VALUES(?,?,?,?) ON CONFLICT(member) DO UPDATE SET enabled=excluded.enabled,permissions=excluded.permissions,generation=excluded.generation',
                    (identity,int(enabled),json.dumps(current),generation+1))
            self.audit(db,author,identity,action)
        self.refresh();self.room.notify()
        return {'ok':True}

    def revoke(self,author,payload):
        self.admin(author); identity=payload.get('device_id'); action=payload.get('action')
        if action in ('enable','permissions'):return self.configure(author,payload)
        if not isinstance(identity,str) or not identity:self.fail(400,'请选择成员')
        if identity=='owner':self.fail(403,'不能删除或归档管理员')
        if action not in ('revoke','archive','delete'):self.fail(400,'不支持的操作')
        if action in ('archive','delete') and payload.get('confirm') is not True:
            self.fail(400,'归档或删除需要明确确认；历史消息不会删除')
        retired_jobs=[]
        with self.room.db() as db:
            db.execute('BEGIN IMMEDIATE')
            device=db.execute('SELECT * FROM devices WHERE id=?',(identity,)).fetchone()
            legacy=self.base_members.get(identity)
            if not device and (not legacy or legacy['role']!='agent'):
                self.fail(404,'Agent 不存在，管理员不能删除')
            retired=db.execute('SELECT state FROM agent_retirements WHERE member=?',(identity,)).fetchone()
            old=device['state'] if device else (retired['state'] if retired else 'active')
            state={'revoke':'revoked','archive':'archived','delete':'deleted'}[action]
            if old=='deleted' or old==state:return {'ok':True,'state':old,'replayed':True}
            if old=='archived' and action=='revoke':self.fail(409,'已归档成员不能恢复，请创建新成员')
            if not device and action=='revoke':self.fail(409,'旧配置成员请使用归档或删除')
            if device:
                db.execute('UPDATE devices SET state=?,credential_hash=NULL,generation=generation+1 WHERE id=?',(state,identity))
                if state in ('archived','deleted'):
                    db.execute('UPDATE devices SET name_key=? WHERE id=?',('\x00retired:'+identity,identity))
                db.execute('UPDATE device_invitations SET revoked=? WHERE device_id=? AND revoked IS NULL',(time.time(),identity))
            else:
                db.execute('INSERT INTO agent_retirements VALUES(?,?,?) ON CONFLICT(member) DO UPDATE SET state=excluded.state,created=excluded.created',(identity,state,time.time()))
            db.execute('DELETE FROM presence WHERE member=?',(identity,))
            if state in ('archived','deleted'):
                retired_jobs=self.retire_jobs(db,identity,state)
            self.audit(db,author,identity,action)
        self.refresh();self.room.notify()
        for job_id in retired_jobs:self.room._arm_lease(job_id,None)
        return {'ok':True,'state':state}

    def retire_jobs(self,db,identity,state):
        jobs=list(db.execute("SELECT * FROM jobs WHERE target=? AND status IN ('pending','running','waiting')",(identity,)))
        now=time.time()
        for job in jobs:
            text='成员已'+('删除' if state=='deleted' else '归档')+'，此未完成任务已标记失败。历史记录保留。'
            reply=db.execute('INSERT INTO messages(author,text,reply_to,depth,created,key,source) VALUES(?,?,?,?,?,?,?)',
                ('hub',text,job['message_id'],0,now,'retire-'+identity+'-'+str(job['id']),'system'))
            db.execute("UPDATE jobs SET status='failed',completed=0,blocked=1,lease=NULL,lease_until=NULL,reply_id=?,finished=? WHERE id=?",(reply.lastrowid,now,job['id']))
        for job in jobs:
            self.room._schedule_summary(db,job['id'],identity)
            if job['team_id']:self.room._advance_team(db,job['team_id'])

        return [job['id'] for job in jobs]
