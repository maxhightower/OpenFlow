"""FastAPI dependency injection: shared singletons for store, service, broadcaster."""

from __future__ import annotations

from chloe.observer.store import UsageStore
from chloe.web.config import WebConfig
from chloe.web.events import EventBroadcaster
from chloe.web.services.project_service import ProjectService
from chloe.web.store import WebStore

# Module-level singletons, initialized in create_app()
_store: WebStore | None = None
_usage_store: UsageStore | None = None
_project_service: ProjectService | None = None
_broadcaster: EventBroadcaster | None = None
_config: WebConfig | None = None


def init_dependencies(config: WebConfig) -> None:
    global _store, _usage_store, _project_service, _broadcaster, _config
    _config = config
    _store = WebStore(db_path=config.db_path)
    # UsageStore uses a db in the same directory as the main db
    usage_db = config.db_path.parent / "usage.db"
    _usage_store = UsageStore(db_path=usage_db)
    _broadcaster = EventBroadcaster()
    _project_service = ProjectService(
        store=_store,
        token_budget=config.token_budget,
        model=config.model,
    )


def shutdown_dependencies() -> None:
    global _store, _usage_store
    if _store:
        try:
            _store.close()
        except Exception:
            pass
        _store = None
    if _usage_store:
        try:
            _usage_store.close()
        except Exception:
            pass
        _usage_store = None


def get_store() -> WebStore:
    assert _store is not None, "Dependencies not initialized"
    return _store


def get_usage_store() -> UsageStore:
    assert _usage_store is not None, "Dependencies not initialized"
    return _usage_store


def get_project_service() -> ProjectService:
    assert _project_service is not None, "Dependencies not initialized"
    return _project_service


def get_broadcaster() -> EventBroadcaster:
    assert _broadcaster is not None, "Dependencies not initialized"
    return _broadcaster


def get_config() -> WebConfig:
    assert _config is not None, "Dependencies not initialized"
    return _config
