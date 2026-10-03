from app.core.config import get_settings
from app.laboratory.repository import Repository


def repository() -> Repository:
    settings = get_settings()
    path = settings.laboratory_database_path or settings.database_path.with_name("laboratory.db")
    if path.resolve() == settings.database_path.resolve():
        raise RuntimeError("El laboratorio requiere una base separada de EMS.")
    return Repository(path)
