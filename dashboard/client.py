import json,os
from urllib.request import urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError,URLError


class EvidenceError(Exception):pass


def get(path,**parameters):
    base=os.environ.get('RCHM_API_URL','http://api:8000').rstrip('/')
    url=base+path+('?' + urlencode(parameters) if parameters else '')
    try:
        with urlopen(url,timeout=15) as response:return json.load(response)
    except HTTPError as error:
        if error.code==409:message='Evidence verification failed. Run the project integrity checks before using this view.'
        elif error.code==404:message='This build, alert or recording is not available in the selected evaluation.'
        else:message='Evidence is temporarily unavailable. Check the local services and retry.'
        raise EvidenceError(message) from None
    except (URLError,TimeoutError,OSError,ValueError):
        raise EvidenceError('The evidence service is unavailable. Check the local services and retry.') from None
