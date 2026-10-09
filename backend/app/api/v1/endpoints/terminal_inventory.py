from fastapi import APIRouter, Depends, Response

from app.api.deps import terminal_user
from app.models import UserPublic
from app.services.terminal_access import visible_devices

router=APIRouter(prefix='/terminal-inventory',tags=['terminal'])


@router.get('')
def resources(response: Response,user: UserPublic=Depends(terminal_user)):
    response.headers['Cache-Control']='no-store'
    return {'schema_version':1,'scope':'authorized_configuration',
            'actions_migration_complete':True,'devices':visible_devices(user)}
