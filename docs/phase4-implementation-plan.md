# Phase 4 Implementation Plan: Architectural Enhancement

**Date:** 2025-11-14
**Objective:** Implement Pydantic validation, SQLAlchemy ORM, and Dependency Injection

---

## Overview

Phase 4 enhances the architectural foundation of MQI Communicator by introducing:
1. **Pydantic Configuration Validation** - Type-safe, validated configuration
2. **SQLAlchemy ORM Migration** - Replace raw SQL with ORM patterns
3. **Dependency Injection Framework** - Automated dependency management

## Implementation Strategy

Following the TDD methodology from Phases 1-3:
- **Red**: Write failing tests first
- **Green**: Implement minimal code to pass tests
- **Refactor**: Improve design while keeping tests green

---

## Sub-Phase 4.1: Pydantic Configuration Validation

### Objectives
- Replace dict-based config with Pydantic models
- Add runtime validation for all configuration values
- Maintain backward compatibility with existing Settings API

### Implementation Steps

#### Step 1: Define Pydantic Config Models (TDD)
**Test First:**
```python
# tests/test_pydantic_config.py
def test_database_config_validation():
    """Test DatabaseConfig validates required fields"""

def test_database_config_defaults():
    """Test DatabaseConfig provides sensible defaults"""

def test_settings_loads_from_yaml():
    """Test Settings can load and validate YAML config"""
```

**Implementation:**
```python
# src/config/pydantic_models.py
from pydantic import BaseModel, Field, validator

class DatabaseConfig(BaseModel):
    connection_timeout_seconds: int = Field(default=30, ge=1, le=300)
    journal_mode: str = Field(default="WAL", pattern="^(DELETE|TRUNCATE|PERSIST|MEMORY|WAL|OFF)$")
    synchronous: str = Field(default="NORMAL", pattern="^(OFF|NORMAL|FULL|EXTRA)$")
    cache_size: int = Field(default=-2000)

class ProcessingConfig(BaseModel):
    max_retries: int = Field(default=3, ge=0)
    retry_delay_seconds: int = Field(default=5, ge=1)

class ProgressTrackingConfig(BaseModel):
    polling_interval_seconds: int = Field(default=5, ge=1)
    coarse_phase_progress: Dict[str, float] = Field(default_factory=dict)

class AppConfig(BaseModel):
    """Root configuration model"""
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)
    progress_tracking: ProgressTrackingConfig = Field(default_factory=ProgressTrackingConfig)
    # ... other config sections
```

#### Step 2: Integrate Pydantic into Settings (TDD)
**Test First:**
```python
def test_settings_validates_invalid_config():
    """Test Settings raises ValidationError for invalid config"""

def test_settings_backward_compatible_api():
    """Test get_database_config() still returns dict for backward compatibility"""
```

**Implementation:**
- Keep existing Settings API intact
- Add internal Pydantic validation
- Provide adapter methods that return dicts

#### Step 3: Add Validation Tests
- Test invalid timeout values
- Test missing required fields
- Test field type mismatches
- Test enum validation

### Success Criteria
✅ All config sections have Pydantic models
✅ Invalid configs are caught at startup
✅ Existing code continues to work (backward compatible)
✅ All tests pass

---

## Sub-Phase 4.2: SQLAlchemy ORM Migration

### Objectives
- Replace raw SQL with SQLAlchemy ORM
- Maintain existing repository interface
- Add relationship management
- Keep all existing tests passing

### Implementation Steps

#### Step 1: Define SQLAlchemy Models (TDD)
**Test First:**
```python
# tests/test_sqlalchemy_models.py
def test_case_model_creation():
    """Test Case ORM model can be created"""

def test_case_beam_relationship():
    """Test Case-Beam one-to-many relationship"""

def test_beam_cascade_delete():
    """Test beams are deleted when case is deleted"""
```

**Implementation:**
```python
# src/database/models.py
from sqlalchemy import Column, String, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()

class Case(Base):
    __tablename__ = 'cases'

    case_id = Column(String, primary_key=True)
    case_path = Column(String, nullable=False)
    status = Column(String, nullable=False)
    progress = Column(Float, default=0.0)
    created_at = Column(DateTime, server_default='CURRENT_TIMESTAMP')
    updated_at = Column(DateTime, server_default='CURRENT_TIMESTAMP')
    error_message = Column(String, nullable=True)
    assigned_gpu = Column(String, ForeignKey('gpu_resources.uuid'), nullable=True)
    interpreter_completed = Column(Boolean, default=False)

    # Relationships
    beams = relationship("Beam", back_populates="case", cascade="all, delete-orphan")
    gpu = relationship("GpuResource", back_populates="assigned_cases")

class Beam(Base):
    __tablename__ = 'beams'

    beam_id = Column(String, primary_key=True)
    parent_case_id = Column(String, ForeignKey('cases.case_id'), nullable=False)
    beam_path = Column(String, nullable=False)
    status = Column(String, nullable=False)
    progress = Column(Float, default=0.0)
    hpc_job_id = Column(String, nullable=True)
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime, server_default='CURRENT_TIMESTAMP')
    updated_at = Column(DateTime, server_default='CURRENT_TIMESTAMP')

    # Relationships
    case = relationship("Case", back_populates="beams")
```

#### Step 2: Create SQLAlchemy Repository Adapter (TDD)
**Test First:**
```python
def test_sqlalchemy_case_repo_add_case():
    """Test SQLAlchemy repo can add cases"""

def test_sqlalchemy_case_repo_get_case():
    """Test SQLAlchemy repo can retrieve cases"""

def test_existing_case_repo_tests_pass():
    """Test all existing CaseRepository tests still pass"""
```

**Implementation:**
```python
# src/repositories/sqlalchemy_case_repo.py
from sqlalchemy.orm import Session
from src.database.models import Case, Beam
from src.domain.models import CaseData, BeamData

class SQLAlchemyCaseRepository(BaseRepository):
    """SQLAlchemy-based implementation of CaseRepository"""

    def __init__(self, session: Session, logger: StructuredLogger):
        self.session = session
        self.logger = logger

    def add_case(self, case_id: str, case_path: Path) -> None:
        case = Case(
            case_id=case_id,
            case_path=str(case_path),
            status=CaseStatus.PENDING.value,
            progress=0.0
        )
        self.session.add(case)
        self.session.commit()
```

#### Step 3: Migrate Existing Repositories
- Create SQLAlchemy versions of all repositories
- Add backward-compatible factory
- Run full test suite
- Gradually migrate call sites

### Success Criteria
✅ All SQLAlchemy models defined
✅ Relationships work correctly
✅ All existing repository tests pass
✅ No breaking changes to existing code

---

## Sub-Phase 4.3: Dependency Injection Framework

### Objectives
- Implement DI container using `dependency-injector`
- Remove manual dependency wiring
- Support testing with mock dependencies
- Maintain clean separation of concerns

### Implementation Steps

#### Step 1: Set Up DI Container (TDD)
**Test First:**
```python
# tests/test_dependency_injection.py
def test_container_provides_database_connection():
    """Test DI container can provide DatabaseConnection"""

def test_container_provides_case_repository():
    """Test DI container can provide CaseRepository"""

def test_container_wires_dependencies_automatically():
    """Test dependencies are auto-wired"""
```

**Implementation:**
```python
# src/infrastructure/container.py
from dependency_injector import containers, providers
from src.config.settings import Settings
from src.database.connection import DatabaseConnection
from src.repositories.case_repo import CaseRepository

class ApplicationContainer(containers.DeclarativeContainer):
    """DI container for the application"""

    config = providers.Configuration()

    # Core services
    settings = providers.Singleton(
        Settings,
        config_path=config.config_path
    )

    logger = providers.Singleton(
        StructuredLogger,
        name="app",
        settings=settings
    )

    # Database
    database = providers.Singleton(
        DatabaseConnection,
        db_path=settings.provided.get_database_path(),
        settings=settings,
        logger=logger
    )

    # Repositories
    case_repository = providers.Factory(
        CaseRepository,
        db_connection=database,
        logger=logger
    )

    # States
    workflow_manager = providers.Factory(
        WorkflowManager,
        case_repo=case_repository,
        settings=settings,
        logger=logger
    )
```

#### Step 2: Wire Application with DI
**Test First:**
```python
def test_main_uses_di_container():
    """Test main.py uses DI container"""

def test_integration_with_di():
    """Test full integration with DI"""
```

**Implementation:**
```python
# main.py
from src.infrastructure.container import ApplicationContainer

def main():
    container = ApplicationContainer()
    container.config.config_path.from_value("config/config.yaml")

    # All dependencies auto-wired
    dispatcher = container.dispatcher()
    dispatcher.run()
```

#### Step 3: Add Test Overrides
```python
# tests/conftest.py
@pytest.fixture
def container():
    """Provide test container with mocked dependencies"""
    container = ApplicationContainer()
    container.database.override(providers.Singleton(MockDatabase))
    return container
```

### Success Criteria
✅ DI container configured
✅ All dependencies auto-wired
✅ Tests use mocked dependencies
✅ No manual dependency wiring in application code

---

## Testing Strategy

### Phase 4.1 Tests
- `tests/test_pydantic_config.py` - Pydantic model validation tests
- `tests/test_settings_pydantic.py` - Settings integration tests

### Phase 4.2 Tests
- `tests/test_sqlalchemy_models.py` - ORM model tests
- `tests/test_sqlalchemy_repositories.py` - Repository tests
- Existing tests must all pass (backward compatibility)

### Phase 4.3 Tests
- `tests/test_dependency_injection.py` - DI container tests
- `tests/test_integration_with_di.py` - Full integration tests

### Regression Testing
- Run ALL existing tests after each sub-phase
- Ensure 47/48 tests still pass (same as Phase 3)
- No breaking changes allowed

---

## Migration Path

### Incremental Approach
1. **Add new code alongside old** - No breaking changes
2. **Test both implementations** - Ensure parity
3. **Gradual migration** - One module at a time
4. **Feature flags** - Allow toggling between old/new
5. **Remove old code** - Only after full migration

### Backward Compatibility
- Keep existing APIs intact
- Add adapters where needed
- Deprecation warnings for old patterns
- Migration guides for developers

---

## Dependencies to Add

```txt
# requirements-phase4.txt
pydantic>=2.0.0
sqlalchemy>=2.0.0
dependency-injector>=4.41.0
```

---

## Success Metrics

### Phase 4.1: Pydantic
- [ ] All config sections have Pydantic models
- [ ] Invalid configs caught at startup
- [ ] Backward compatibility maintained
- [ ] Tests: +10 new tests, all pass

### Phase 4.2: SQLAlchemy
- [ ] All tables have ORM models
- [ ] All repositories use SQLAlchemy
- [ ] Relationships work correctly
- [ ] Tests: +15 new tests, all pass

### Phase 4.3: DI
- [ ] DI container fully configured
- [ ] All dependencies auto-wired
- [ ] Test overrides work
- [ ] Tests: +8 new tests, all pass

### Overall
- [ ] Total new tests: +33
- [ ] All existing tests pass (47/48)
- [ ] No breaking changes
- [ ] Documentation updated

---

## Rollback Plan

If Phase 4 introduces issues:
1. Feature flag to disable new code
2. Revert to previous commit
3. Fix issues in separate branch
4. Re-test thoroughly
5. Merge again

---

## Timeline Estimate

- **Sub-Phase 4.1 (Pydantic)**: ~3-4 hours
- **Sub-Phase 4.2 (SQLAlchemy)**: ~5-6 hours
- **Sub-Phase 4.3 (DI)**: ~3-4 hours
- **Integration & Testing**: ~2-3 hours
- **Documentation**: ~1-2 hours
- **Total**: ~14-19 hours

---

## Next Steps

1. ✅ Create this implementation plan
2. Start Sub-Phase 4.1: Implement Pydantic configuration validation
3. Proceed to Sub-Phase 4.2: SQLAlchemy ORM migration
4. Complete Sub-Phase 4.3: Dependency Injection framework
5. Run full regression test suite
6. Document all changes
7. Commit and push

---

**Document Version:** 1.0
**Status:** Draft → Ready for Implementation
