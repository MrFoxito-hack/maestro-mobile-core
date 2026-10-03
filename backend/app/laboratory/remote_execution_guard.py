"""Root-owned remote ownership for the fixed QoE worker transport.

Not a public command endpoint. All input comes from the SSH-authenticated server
adapter. The PCF and unrelated EMS writers are outside this guard's domain.
"""
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
import time

DIRECTORY = '/var/lib/maestro-laboratory/qoe-owner'


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS owner(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
      execution_id TEXT, token TEXT, boot TEXT, state TEXT, expires REAL, generation INTEGER);
    CREATE TABLE IF NOT EXISTS retired(execution_id TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY, execution_id TEXT, token TEXT,
      process_group INTEGER, boot TEXT, intent_at REAL, completed INTEGER DEFAULT 0);
    ''')


def group_alive(group):
    for proc in pathlib.Path('/proc').iterdir():
        if not proc.name.isdigit(): continue
        try:
            fields=(proc/'stat').read_text().rsplit(')',1)[1].split()
            if fields[0] != 'Z' and int(fields[2]) == group: return True
        except (OSError, ValueError, IndexError): pass
    return False


def control(db, request, *, clock=time.monotonic, boot=None, alive=group_alive):
    execution, token = request['execution_id'], request['token']
    generation=request['generation']
    if type(generation) is not int or generation<1: raise ValueError('invalid_generation')
    if not re.fullmatch('[a-f0-9]{32}', execution) or not re.fullmatch('[a-f0-9]{32}', token):
        raise ValueError('invalid_owner')
    now=clock()
    boot=boot or pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    row=db.execute('SELECT execution_id,token,boot,state,expires,generation FROM owner WHERE singleton=1').fetchone()
    operation=request['operation']
    if operation=='state':
        return {'this_execution':bool(row and row[0]==execution),'state':row[3] if row else 'empty'}
    if operation=='release' and row and row[0]==execution and row[3]=='released':
        return {'released':True}
    if operation in ('acquire','recover'):
        if db.execute('SELECT 1 FROM retired WHERE execution_id=?',(execution,)).fetchone():
            raise ValueError('execution_retired')
        if row and row[3] != 'released':
            if row[0] != execution: raise ValueError('remote_resource_reserved')
            if generation<row[5] or (generation==row[5] and token!=row[1]):
                raise ValueError('stale_generation')
            if operation != 'recover' and (row[1] != token or row[2] != boot or row[4] <= now):
                raise ValueError('remote_recovery_required')
            for group, epoch in db.execute('SELECT process_group,boot FROM actions WHERE completed=0'):
                if epoch == boot and alive(group): raise ValueError('previous_command_still_running')
        db.execute('INSERT OR REPLACE INTO owner VALUES(1,?,?,?,?,?,?)',
                   (execution,token,boot,'recovering' if operation=='recover' else 'active',now+120,generation))
        db.commit()
        return {'owner_verified':True,'state':'recovering' if operation=='recover' else 'active'}
    if not row or row[:3] != (execution,token,boot) or row[5]!=generation or row[3]=='released' or row[4] <= now:
        raise ValueError('remote_ownership_lost')
    if operation=='release':
        for group, epoch in db.execute('SELECT process_group,boot FROM actions WHERE completed=0'):
            if epoch==boot and alive(group): raise ValueError('previous_command_still_running')
        if request.get('recovery_verified') is not True: raise ValueError('recovery_not_verified')
        db.execute('UPDATE actions SET completed=2 WHERE completed=0')
        db.execute("UPDATE owner SET state='released' WHERE singleton=1")
        db.execute('INSERT INTO retired VALUES(?)',(execution,))
        db.commit()
        return {'released':True}
    if operation=='run':
        action=request['action_id']
        if not re.fullmatch('[a-f0-9]{32}', action): raise ValueError('invalid_action_id')
        if db.execute('SELECT 1 FROM actions WHERE id=?',(action,)).fetchone():
            raise ValueError('remote_action_already_attempted')
        if row[3] != 'recovering' and db.execute('SELECT 1 FROM actions WHERE completed=0').fetchone():
            raise ValueError('unknown_remote_action_requires_recovery')
        db.execute('UPDATE owner SET expires=? WHERE singleton=1',(now+120,))
        db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,0)',(action,execution,token,os.getpgrp(),boot,now))
        db.commit()
        return {'admitted':True}
    raise ValueError('unsupported_guard_operation')


def main():
    import fcntl
    import pwd
    request=json.loads(sys.argv[1])
    os.umask(0o077)
    root=pathlib.Path(DIRECTORY)
    root.mkdir(parents=True,exist_ok=True)
    if root.is_symlink() or root.stat().st_uid != 0: raise ValueError('unsafe_guard_directory')
    with (root/'lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        with sqlite3.connect(root/'guard.sqlite3') as db:
            initialize(db)
            reply=control(db,request)
            if request['operation']!='run':
                print(json.dumps(reply));return
            command=request['argv']
            if not isinstance(command,list) or not command or any(not isinstance(a,str) for a in command):
                raise ValueError('invalid_server_command')
            timeout=request['timeout']
            if type(timeout) is not int or not 1<=timeout<=100: raise ValueError('invalid_command_deadline')
            def demote():
                if not request['privileged']:
                    account=pwd.getpwnam(request['unix_user'])
                    os.initgroups(account.pw_name,account.pw_gid)
                    os.setgid(account.pw_gid);os.setuid(account.pw_uid)
            # Same process group as the outer remote timeout: expiry kills children.
            process=subprocess.run(['timeout','--kill-after=2',str(timeout),*command],
                stdin=subprocess.DEVNULL,preexec_fn=demote)
            db.execute('UPDATE actions SET completed=1 WHERE id=?',(request['action_id'],))
            db.commit()
            raise SystemExit(process.returncode)


if __name__=='__main__':
    try: main()
    except Exception as error:
        print(json.dumps({'guard_error': str(error) if isinstance(error,ValueError) else 'remote_guard_failed'}),file=sys.stderr)
        raise SystemExit(73)
