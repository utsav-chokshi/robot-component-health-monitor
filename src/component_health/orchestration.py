"""Prefect-only environment launches the locked core worker as a child process."""
import subprocess
import sys
from prefect import flow


@flow(name='ims-recording-features',persist_result=False)
def feature_flow():
    # No credentials, engine objects or input measurements become flow parameters.
    subprocess.run(sys.argv[1:],check=True)


if __name__=='__main__':
    if len(sys.argv)<2: raise SystemExit('Worker command required')
    feature_flow()
