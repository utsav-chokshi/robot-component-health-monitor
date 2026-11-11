import json,os,subprocess
from pathlib import Path


def test_prefect_server_interface_is_isolated_from_application():
    executable=os.environ.get('RCHM_ORCHESTRATOR_PYTHON','/opt/orchestrator/bin/python')
    assert Path(executable).is_file()
    result=subprocess.run([executable,'-c',"import json,fastapi,prefect; from prefect.server.api.server import SubprocessASGIServer; print(json.dumps({'fastapi':fastapi.__version__,'prefect':prefect.__version__,'legacy_body': fastapi.Body(...,min_items=1) is not None}))"],check=True,capture_output=True,text=True)
    metadata=json.loads(result.stdout)
    assert metadata=={'fastapi':'0.139.0','prefect':'3.8.8','legacy_body':True}
    import fastapi
    assert fastapi.__version__=='0.162.2'
