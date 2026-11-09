"""Password sessions for the private LAN application."""
import hashlib
import hmac
import secrets
import time
from pathlib import Path

COOKIE="metering_session"

def password_hash(password,salt=None):
    salt=salt or secrets.token_hex(16)
    digest=hashlib.scrypt(password.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex()
    return salt+":"+digest

def verify(password,stored):return hmac.compare_digest(password_hash(password,stored.split(":")[0]),stored)

class Security:
    def __init__(self,store):
        self.store=store;self.sessions={};self.attempts={}
        if not store.get("password"):
            password=secrets.token_urlsafe(20)
            store.set("password",password_hash(password))
            path=store.directory/"initial-access.txt"
            path.write_text("Username: admin\nPassword: "+password+"\nChange this password in Settings after first login.\n")
            path.chmod(0o600)
    def login(self,password,client):
        now=time.time();attempts=[t for t in self.attempts.get(client,[]) if t>now-300]
        if len(attempts)>=10:raise ValueError("Too many login attempts; try again in five minutes")
        attempts.append(now);self.attempts[client]=attempts
        if not verify(password,self.store.get("password")):return None
        self.attempts.pop(client,None)
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(24)
        self.sessions[token]={"expires":now+8*3600,"csrf":csrf}
        # Keep session state bounded; expiration does not require a cleanup job.
        self.sessions={k:v for k,v in self.sessions.items() if v["expires"]>now}
        return token,csrf
    def session(self,token):
        session=self.sessions.get(token)
        return session if session and session["expires"]>time.time() else None
