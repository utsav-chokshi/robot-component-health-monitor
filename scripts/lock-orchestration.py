import hashlib,re,subprocess,tempfile
from pathlib import Path
root=Path('/workspace')
with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp)
    subprocess.run(['python','-m','pip','download','--only-binary=:all:','--dest',str(tmp),'-r',str(root/'requirements-orchestration.in')],check=True)
    lines=['# Isolated Prefect server environment; Linux/amd64 CPython 3.12 wheel hashes.']
    for wheel in sorted(tmp.glob('*.whl')):
        name,version,*_=wheel.name.split('-');name=re.sub('[-_.]+','-',name).lower()
        lines.append(f'{name}=={version} --hash=sha256:{hashlib.sha256(wheel.read_bytes()).hexdigest()}')
    (root/'requirements-orchestration.lock').write_text('\n'.join(lines)+'\n')
    print('Orchestration environment locked',len(lines)-1)
