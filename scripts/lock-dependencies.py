"""Resolve hashed Linux/amd64 CPython 3.12 wheels in the pinned base image."""
import hashlib,re,subprocess,tempfile
from pathlib import Path
root=Path('/workspace')
for group in ['build','runtime','test']:
    source=root/({'runtime':'requirements.in'}.get(group,f'requirements-{group}.in'))
    destination=root/({'runtime':'requirements.lock'}.get(group,f'requirements-{group}.lock'))
    with tempfile.TemporaryDirectory() as temporary:
        temporary=Path(temporary)
        command=['python','-m','pip','download','--only-binary=:all:','--dest',str(temporary),'-r',str(source)]
        if group=='test':
            constraint=temporary/'constraints.txt'
            constraint.write_text('\n'.join(line.split(' --hash=')[0] for line in (root/'requirements.lock').read_text().splitlines() if line and not line.startswith('#'))+'\n')
            command+=['-c',str(constraint)]
        subprocess.run(command,check=True)
        lines=['# Linux/amd64 CPython 3.12 wheel lock. Regenerate with scripts/lock-dependencies.py in the pinned base image.']
        for file in sorted(temporary.glob('*.whl')):
            name,version,*_=file.name.split('-')
            name=re.sub('[-_.]+','-',name).lower()
            with file.open('rb') as stream:checksum=hashlib.file_digest(stream,'sha256').hexdigest()
            lines.append(f'{name}=={version} --hash=sha256:{checksum}')
        if len(lines)==1:raise RuntimeError('No wheels resolved')
        destination.write_text('\n'.join(lines)+'\n')
        print(f'{group}: locked {len(lines)-1} wheels',flush=True)
