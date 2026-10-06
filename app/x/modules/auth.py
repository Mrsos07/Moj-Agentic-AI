import os
from functools import wraps
from flask import request, jsonify, session, redirect, url_for


#: قيم منشورة في المستودع — لا تُقبل توكناً أبداً (ولو ضُبطت صراحةً)
_WEAK = {'your-secure-token-here', 'change-me'}


def get_tokens():
    # بلا قيمة افتراضية: الخادم يرث السرّ المولَّد من العملية الأم (app.core.internal_secrets).
    # تشغيله وحده بلا سرّ = لا توكن صالح (رفض الكل) لا قبول توكن منشور على GitHub.
    raw = os.getenv('XSUITE_API_TOKENS', '')
    tokens = {t.strip() for t in raw.split(',') if t.strip()}
    return tokens - _WEAK


def require_api_token(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        if not auth.lower().startswith('bearer '):
            return jsonify({'success': False, 'error': 'Missing Bearer token'}), 401
        token = auth.split(' ', 1)[1].strip()
        if token not in get_tokens():
            return jsonify({'success': False, 'error': 'Invalid token'}), 403
        return fn(*args, **kwargs)
    return wrapper


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('system_login'))
        return fn(*args, **kwargs)
    return wrapper
