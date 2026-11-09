"""SQL passwords stay encrypted in the private state directory and never round-trip to browsers."""
import copy
import json
import os
from cryptography.fernet import Fernet, InvalidToken
from .sql_models import SqlSettings

class SqlSecrets:
    def __init__(self,store):
        self.store=store
        with store.lock,store.db:
            store.db.execute("CREATE TABLE IF NOT EXISTS sql_credentials (source_id TEXT PRIMARY KEY, secret BLOB NOT NULL)")
    def cipher(self):
        path=self.store.directory/"sql-credentials.key"
        if not path.exists():
            with self.store.lock:
                if self.store.db.execute("SELECT 1 FROM sql_credentials LIMIT 1").fetchone():
                    raise ValueError("SQL credential key is missing; restore it from the private backup")
            try:
                fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,"wb") as f:f.write(Fernet.generate_key())
            except FileExistsError:pass
        return Fernet(path.read_bytes())
    def exists(self,source_id):
        with self.store.lock:return self.store.db.execute("SELECT 1 FROM sql_credentials WHERE source_id=?",(source_id,)).fetchone() is not None
    def password(self,source_id):
        with self.store.lock:r=self.store.db.execute("SELECT secret FROM sql_credentials WHERE source_id=?",(source_id,)).fetchone()
        if not r:raise ValueError("Enter a password in this SQL source's connection settings")
        try:return self.cipher().decrypt(r[0]).decode()
        except InvalidToken:raise ValueError("SQL credentials cannot be decrypted; restore the key or re-enter the password") from None
    def public_config(self,config):
        data=copy.deepcopy(config.model_dump())
        for source in data["sources"]:
            if source["kind"]=="sql_server":
                source["settings"].pop("password",None)
                source["settings"]["password_set"]=self.exists(source["id"])
        return data
    def prepare(self,config):
        updates={}
        for source in config.sources:
            if source.kind!="sql_server":continue
            settings=dict(source.settings)
            password=settings.pop("password",None);settings.pop("password_set",None)
            try:validated=SqlSettings.model_validate(settings)
            except Exception as e:
                if hasattr(e,"errors"):
                    fields=", ".join(".".join(map(str,x["loc"])) or "settings" for x in e.errors())
                    raise ValueError("Check SQL connection settings: "+fields) from None
                raise ValueError("Check the SQL connection settings") from None
            if password is not None and (not isinstance(password,str) or len(password)>2048):raise ValueError("SQL password must be a string of at most 2048 characters")
            if password:
                updates[source.id]=self.cipher().encrypt(password.encode())
            elif source.enabled and not self.exists(source.id):raise ValueError("Enter the SQL source password before enabling this connection")
            source.settings=validated.model_dump()
        return updates
    def write(self,updates,config):
        # Caller holds the configuration transaction and revision lock.
        for sid,sealed in updates.items():
            self.store.db.execute("INSERT INTO sql_credentials VALUES(?,?) ON CONFLICT(source_id) DO UPDATE SET secret=excluded.secret",(sid,sealed))
        keep={s.id for s in config.sources if s.kind=="sql_server"}
        for (sid,) in self.store.db.execute("SELECT source_id FROM sql_credentials").fetchall():
            if sid not in keep:self.store.db.execute("DELETE FROM sql_credentials WHERE source_id=?",(sid,))
        if updates:
            version=self.store.get("sql_credential_revision",0)+1
            self.store.db.execute("INSERT INTO settings VALUES('sql_credential_revision',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(version),))
