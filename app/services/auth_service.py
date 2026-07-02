"""
Deprecated: This file is kept for backward compatibility.
New code should import from app.services.auth.service instead.
"""
from app.services.auth.service import (
    AuthService,
    hash_password,
    verify_password,
)

# Forward all functions and classes
create_access_token = AuthService.create_access_token
decode_access_token = AuthService.decode_access_token
create_user = AuthService.create_user
authenticate_user = AuthService.authenticate_user
get_current_user = AuthService.get_current_user
require_admin = AuthService.require_admin
websocket_user = AuthService.websocket_user
write_audit = AuthService.write_audit

__all__ = [
    'hash_password',
    'verify_password',
    'create_access_token',
    'decode_access_token',
    'create_user',
    'authenticate_user',
    'get_current_user',
    'require_admin',
    'websocket_user',
    'write_audit',
]
