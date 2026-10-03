from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError

from app.core.security import decode_token
from app.db import connection
from app.models import Role, UserPublic

bearer = HTTPBearer(auto_error=False)


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> UserPublic:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Autenticación requerida")
    try:
        payload = decode_token(credentials.credentials)
    except InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")
    with connection() as conn:
        row = conn.execute("SELECT username,role,testbed,assigned_imsi,enabled FROM users WHERE username=?", (payload["sub"],)).fetchone()
    if not row or not row["enabled"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario inactivo")
    assigned_imsi = row["assigned_imsi"] if "assigned_imsi" in row.keys() else None
    return UserPublic(username=row["username"], role=row["role"], testbed=row["testbed"], assigned_imsi=assigned_imsi)


def require_roles(*roles: Role) -> Callable:
    def dependency(user: UserPublic = Depends(current_user)) -> UserPublic:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permisos insuficientes")
        return user
    return dependency


operator_user = require_roles(Role.admin, Role.teacher)
terminal_user = require_roles(Role.admin, Role.teacher, Role.student)
