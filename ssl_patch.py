"""
ssl_patch.py
============
Apply certifi CA bundle fix for PyInstaller / slim Docker images.
Import this module BEFORE any network code.
"""
import os

def apply():
    try:
        import ssl, certifi
        _ca = certifi.where()
        os.environ['SSL_CERT_FILE']      = _ca
        os.environ['REQUESTS_CA_BUNDLE'] = _ca
        _orig = ssl.create_default_context
        def _patched(*args, **kwargs):
            if not any(k in kwargs for k in ('cafile', 'cadata', 'capath')):
                kwargs['cafile'] = _ca
            return _orig(*args, **kwargs)
        ssl.create_default_context = _patched
    except Exception:
        pass
