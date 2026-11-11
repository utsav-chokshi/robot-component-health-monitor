import hashlib,re,subprocess,tempfile
from pathlib import Path
root=Path('/workspace')
with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp)
    constraint=tmp/'constraints.txt'
    constraint.write_text('\n'.join(line.split(' --hash=')[0] for line in (root/'requirements-test.lock').read_text().splitlines() if line and not line.startswith('#'))+'\n')
    subprocess.run(['python','-m','pip','download','--only-binary=:all:','--dest',str(tmp),'-r',str(root/'requirements-pipeline.in'),'-c',str(constraint)],check=True)
    lines=['# Linux/amd64 CPython 3.12 wheel lock; constrained to tested runtime/test versions.']
    versions={}
    for wheel in sorted(tmp.glob('*.whl')):
        name,version,*_=wheel.name.split('-');name=re.sub('[-_.]+','-',name).lower();versions[name]=version
        lines.append(f'{name}=={version} --hash=sha256:{hashlib.sha256(wheel.read_bytes()).hexdigest()}')
    (root/'requirements-pipeline.lock').write_text('\n'.join(lines)+'\n')
    (root/'requirements-pipeline.in').write_text('\n'.join(f'{name}=={versions[name]}' for name in ['numpy','pyarrow','duckdb','jsonschema'])+'\n')
    print('Pipeline wheels locked:',len(versions))
