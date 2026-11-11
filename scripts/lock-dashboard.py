import hashlib,re,subprocess,tempfile
from pathlib import Path
root=Path('/workspace')
for group in ['dashboard','dashboard-test']:
    with tempfile.TemporaryDirectory() as tmp:
        tmp=Path(tmp);command=['python','-m','pip','download','--only-binary=:all:','--dest',str(tmp),'-r',str(root/f'requirements-{group}.in')]
        if group=='dashboard-test':
            constraint=tmp/'constraints.txt';constraint.write_text('\n'.join(line.split(' --hash=')[0] for line in (root/'requirements-dashboard.lock').read_text().splitlines() if line and not line.startswith('#'))+'\n')
            command+=['-c',str(constraint)]
        subprocess.run(command,check=True);lines=['# Isolated dashboard Linux/amd64 CPython 3.12 hashed wheel lock.'];versions={}
        for wheel in sorted(tmp.glob('*.whl')):
            name,version,*_=wheel.name.split('-');name=re.sub('[-_.]+','-',name).lower();versions[name]=version
            lines.append(f'{name}=={version} --hash=sha256:{hashlib.sha256(wheel.read_bytes()).hexdigest()}')
        (root/f'requirements-{group}.lock').write_text('\n'.join(lines)+'\n')
        if group=='dashboard':(root/'requirements-dashboard.in').write_text('\n'.join(f'{name}=={versions[name]}' for name in ['streamlit','altair','pandas'])+'\n')
        print(group,'locked',len(versions),flush=True)
