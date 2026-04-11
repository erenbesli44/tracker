# FastAPI Best Practices Checklist — Tracker Project

Consolidated from [zhanymkanov/fastapi-best-practices](https://github.com/zhanymkanov/fastapi-best-practices) and the official FastAPI SKILL.md.

---

## Project Structure (Domain-Based)

```
tracker/
├── src/
│   ├── persons/
│   │   ├── router.py        # endpoints
│   │   ├── schemas.py       # pydantic models
│   │   ├── models.py        # SQLModel db models
│   │   ├── service.py       # business logic
│   │   ├── dependencies.py  # request validation
│   │   ├── constants.py     # error codes, enums
│   │   └── exceptions.py    # PersonNotFound, etc.
│   ├── videos/
│   │   ├── router.py
│   │   ├── schemas.py
│   │   ├── models.py
│   │   ├── service.py
│   │   ├── dependencies.py
│   │   └── exceptions.py
│   ├── topics/
│   │   ├── router.py
│   │   ├── schemas.py
│   │   ├── models.py
│   │   ├── service.py
│   │   ├── dependencies.py
│   │   └── exceptions.py
│   ├── classification/
│   │   ├── router.py
│   │   ├── schemas.py
│   │   ├── service.py       # classification logic + prompt
│   │   ├── dependencies.py
│   │   └── constants.py     # prompt templates
│   ├── timeline/
│   │   ├── router.py        # timeline query endpoints
│   │   ├── schemas.py
│   │   └── service.py
│   ├── config.py             # global settings (BaseSettings)
│   ├── database.py           # SQLite/SQLModel engine setup
│   ├── models.py             # shared base model
│   ├── exceptions.py         # global exception handlers
│   └── main.py               # FastAPI app init
├── tests/
│   ├── persons/
│   ├── videos/
│   ├── topics/
│   ├── classification/
│   └── conftest.py
├── dummy/                     # transcript examples for dev
├── docs/                      # architecture docs
├── pyproject.toml
├── .env
└── .gitignore
```

---

## Rules to Follow

### 1. Always Use `Annotated`
```python
# YES
item_id: Annotated[int, Path(ge=1)]
# NO
item_id: int = Path(ge=1)
```

### 2. Create Dependency Type Aliases
```python
CurrentPersonDep = Annotated[Person, Depends(valid_person_id)]
```

### 3. No Ellipsis for Required Fields
```python
# YES
name: str
price: float = Field(gt=0)
# NO
name: str = ...
price: float = Field(..., gt=0)
```

### 4. Return Types on All Endpoints
```python
@router.get("/persons/{person_id}")
async def get_person(person_id: int) -> PersonResponse: ...
```

### 5. Router-Level Prefix and Tags
```python
router = APIRouter(prefix="/persons", tags=["persons"])
```

### 6. One HTTP Operation Per Function

### 7. Use `def` (sync) for Sync DB Operations
SQLite operations are blocking → use `def` not `async def` with SQLModel sync engine.

### 8. Use Dependencies for Validation
```python
async def valid_person_id(person_id: int) -> Person:
    person = service.get_by_id(person_id)
    if not person:
        raise PersonNotFound()
    return person
```

### 9. Custom Base Model with Datetime Handling
```python
from pydantic import BaseModel, ConfigDict

class CustomModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
    )
```

### 10. Decouple Settings Per Domain
```python
# src/config.py — global
class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./tracker.db"
    ENVIRONMENT: str = "local"
```

### 11. DB Naming Conventions
- `lower_case_snake`
- Singular form: `person`, `video`, `topic`
- `_at` suffix for datetime: `created_at`, `updated_at`
- `_date` suffix for date

### 12. Use SQLModel (not raw SQLAlchemy)

### 13. Use Ruff for Linting/Formatting

### 14. Use uv for Dependency Management

### 15. Set `pyproject.toml` entrypoint
```toml
[tool.fastapi]
entrypoint = "src.main:app"
```

### 16. Async Test Client from Day 0
```python
import pytest
from httpx import AsyncClient, ASGITransport
from src.main import app

@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as client:
        yield client
```

### 17. Cross-Module Imports with Explicit Module Names
```python
from src.persons import service as persons_service
from src.topics import constants as topics_constants
```

### 18. Do NOT Use Pydantic RootModel

### 19. Do NOT Use ORJSONResponse / UJSONResponse
