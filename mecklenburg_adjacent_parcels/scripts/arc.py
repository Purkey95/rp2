"""Minimal ArcGIS REST query helper (browser UA required by meckgis)."""
import json, time, urllib.parse, urllib.request

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def get(url, params, timeout=120, retries=4):
    body = urllib.parse.urlencode(params).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # noqa: BLE001 - network flakiness
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    return None


def query(layer_url, **params):
    params.setdefault("f", "json")
    d = get(layer_url + "/query", params)
    if isinstance(d, dict) and "error" in d:
        raise RuntimeError(d["error"])
    return d
