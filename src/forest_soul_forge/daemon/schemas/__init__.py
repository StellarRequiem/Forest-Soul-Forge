"""Daemon Pydantic schemas, organized by domain.

Originally a single ~1139-LoC file at ``schemas.py``. Split out by
R1 of the post-marathon roadmap (audit recommendation #2). The package
preserves backward compatibility — every existing
``from forest_soul_forge.daemon.schemas import X`` continues to resolve
because each domain submodule re-exports through this ``__init__``.

Adding new schemas: pick the right submodule (or create a new one for a
new domain), add the class there, then re-export it via the explicit
``from .X import Y`` lines below.
"""

from forest_soul_forge.daemon.schemas.agents import (
    AgentOut,
    AgentListOut,
    TraitProfileIn,
    ToolRefIn,
    BirthRequest,
    HardwareUnbindRequest,
    HardwareUnbindResponse,
    SpawnRequest,
    ArchiveRequest,
    TriuneBondRequest,
    TriuneBondResponse,
)
from forest_soul_forge.daemon.schemas.audit import (
    AuditEventOut,
    AuditListOut,
    CeremonyEmitRequest,
    CeremonyEmitResponse,
)
from forest_soul_forge.daemon.schemas.health import (
    ProviderHealthOut,
    StartupDiagnostic,
    HealthOut,
    ProviderInfoOut,
    SetProviderIn,
    GenerateRequest,
    GenerateResponse,
)
from forest_soul_forge.daemon.schemas.traits import (
    TraitOut,
    SubdomainOut,
    DomainOut,
    RoleOut,
    FlaggedCombinationOut,
    TraitTreeOut,
)
from forest_soul_forge.daemon.schemas.tools import (
    ToolDefOut,
    ArchetypeBundleOut,
    ToolCatalogOut,
    RegisteredToolOut,
    RegisteredToolsOut,
    ResolvedToolOut,
    ResolvedKitOut,
)
from forest_soul_forge.daemon.schemas.genres import (
    GenreRiskProfileOut,
    GenreOut,
    GenresOut,
)
from forest_soul_forge.daemon.schemas.character import (
    CharacterIdentity,
    CharacterPersonality,
    CharacterLoadoutTool,
    CharacterLoadout,
    CharacterCapabilities,
    CharacterPolicySummary,
    CharacterStatsPerTool,
    CharacterStats,
    CharacterMemory,
    CharacterBenchmarks,
    CharacterProvenance,
    CharacterSheetOut,
)
from forest_soul_forge.daemon.schemas.preview import (
    DomainGradeOut,
    GradeReportOut,
    PreviewRequest,
    PreviewResponse,
)
from forest_soul_forge.daemon.schemas.dispatch import (
    TaskCaps,
    ToolCallRequest,
    ToolCallResultOut,
    PendingApprovalOut,
    PendingApprovalListOut,
    ApproveRequest,
    RejectRequest,
    ToolCallResponse,
)
from forest_soul_forge.daemon.schemas.skills import (
    SkillStepSummaryOut,
    SkillSummaryOut,
    SkillCatalogOut,
    SkillRunRequest,
    SkillRunResponse,
)
from forest_soul_forge.daemon.schemas.memory import (
    MemoryConsentGrantRequest,
    MemoryConsentGrantResponse,
    MemoryConsentOut,
    MemoryConsentListResponse,
)

__all__ = [
    "AgentOut",
    "AgentListOut",
    "TraitProfileIn",
    "ToolRefIn",
    "BirthRequest",
    "HardwareUnbindRequest",
    "HardwareUnbindResponse",
    "SpawnRequest",
    "ArchiveRequest",
    "TriuneBondRequest",
    "TriuneBondResponse",
    "AuditEventOut",
    "AuditListOut",
    "CeremonyEmitRequest",
    "CeremonyEmitResponse",
    "ProviderHealthOut",
    "StartupDiagnostic",
    "HealthOut",
    "ProviderInfoOut",
    "SetProviderIn",
    "GenerateRequest",
    "GenerateResponse",
    "TraitOut",
    "SubdomainOut",
    "DomainOut",
    "RoleOut",
    "FlaggedCombinationOut",
    "TraitTreeOut",
    "ToolDefOut",
    "ArchetypeBundleOut",
    "ToolCatalogOut",
    "RegisteredToolOut",
    "RegisteredToolsOut",
    "ResolvedToolOut",
    "ResolvedKitOut",
    "GenreRiskProfileOut",
    "GenreOut",
    "GenresOut",
    "CharacterIdentity",
    "CharacterPersonality",
    "CharacterLoadoutTool",
    "CharacterLoadout",
    "CharacterCapabilities",
    "CharacterPolicySummary",
    "CharacterStatsPerTool",
    "CharacterStats",
    "CharacterMemory",
    "CharacterBenchmarks",
    "CharacterProvenance",
    "CharacterSheetOut",
    "DomainGradeOut",
    "GradeReportOut",
    "PreviewRequest",
    "PreviewResponse",
    "TaskCaps",
    "ToolCallRequest",
    "ToolCallResultOut",
    "PendingApprovalOut",
    "PendingApprovalListOut",
    "ApproveRequest",
    "RejectRequest",
    "ToolCallResponse",
    "SkillStepSummaryOut",
    "SkillSummaryOut",
    "SkillCatalogOut",
    "SkillRunRequest",
    "SkillRunResponse",
    "MemoryConsentGrantRequest",
    "MemoryConsentGrantResponse",
    "MemoryConsentOut",
    "MemoryConsentListResponse",
]
