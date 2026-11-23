"""DJ Lounge lifecycle, invite-code join, and Host moderation endpoints (Module 5/6 — PRD Section 7.3.1, REQ-007, REQ-015, REQ-016, REQ-019, REQ-025, REQ-057, REQ-070, REQ-076)."""

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_host_or_above
from app.db.session import get_db
from app.models.user import User
from app.schemas.lounge import (
    LoungeCreateRequest,
    LoungeCreateResponse,
    LoungeDetailResponse,
    LoungeEndResponse,
    LoungeJoinResponse,
    LoungeListResponse,
    LoungeModerationRequest,
    LoungeModerationResponse,
    LoungeStartResponse,
)
from app.services.lounge_service import (
    create_lounge,
    end_lounge,
    get_lounge_detail,
    join_lounge_by_invite_code,
    list_lounges,
    moderate_lounge,
    start_lounge,
)

router = APIRouter(tags=["DJ Lounges & Host Moderation"])


@router.post(
    "/lounges",
    response_model=LoungeCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new DJ Lounge (Mandatory PRD Section 7.3.1 Contract)",
)
async def create_dj_lounge(
    payload: LoungeCreateRequest,
    host_user: User = Depends(require_host_or_above),
    db: AsyncSession = Depends(get_db),
) -> LoungeCreateResponse:
    """Create a DJ Lounge with seed track and room rules, returning the exact PRD Section 7.3.1 201 contract (REQ-057)."""
    return await create_lounge(db=db, host_user=host_user, payload=payload)


@router.get(
    "/lounges",
    response_model=LoungeListResponse,
    status_code=status.HTTP_200_OK,
    summary="List active, scheduled, and ended DJ Lounges",
)
async def list_dj_lounges(
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="Optional lounge status filter: scheduled | live | ended",
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LoungeListResponse:
    """Return DJ Lounges ordered by creation time descending."""
    return await list_lounges(db=db, status_filter=status_filter)


@router.get(
    "/lounges/{id}",
    response_model=LoungeDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get full current state of a DJ Lounge",
)
async def get_dj_lounge(
    id: str = Path(..., min_length=1, max_length=64),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LoungeDetailResponse:
    """Fetch complete state for a DJ Lounge including Now Playing, Next Up, participants, and moderation state."""
    return await get_lounge_detail(db=db, lounge_id=id.strip())


@router.post(
    "/lounges/join/{inviteCode}",
    response_model=LoungeJoinResponse,
    status_code=status.HTTP_200_OK,
    summary="Join a DJ Lounge via invite code (e.g. SP-9X2L)",
)
async def join_dj_lounge(
    inviteCode: str = Path(..., min_length=1, max_length=24),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LoungeJoinResponse:
    """Join a DJ Lounge by invite code enforcing lock (403), removal (403), and maxParticipants capacity (409) (REQ-019)."""
    return await join_lounge_by_invite_code(
        db=db,
        invite_code=inviteCode,
        user=current_user,
    )


@router.post(
    "/lounges/{id}/start",
    response_model=LoungeStartResponse,
    status_code=status.HTTP_200_OK,
    summary="Start a scheduled DJ Lounge session (Host/Admin only)",
)
async def start_dj_lounge(
    id: str = Path(..., min_length=1, max_length=64),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LoungeStartResponse:
    """Transition a DJ Lounge from 'scheduled' to 'live' (Host/Admin only, REQ-015)."""
    return await start_lounge(
        db=db,
        lounge_id=id.strip(),
        current_user=current_user,
    )


@router.post(
    "/lounges/{id}/end",
    response_model=LoungeEndResponse,
    status_code=status.HTTP_200_OK,
    summary="End a DJ Lounge session and persist session summary (Host/Admin only)",
)
async def end_dj_lounge(
    id: str = Path(..., min_length=1, max_length=64),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LoungeEndResponse:
    """End a DJ Lounge session and persist session duration and peak participant counts (REQ-015)."""
    return await end_lounge(
        db=db,
        lounge_id=id.strip(),
        current_user=current_user,
    )


@router.post(
    "/lounges/{id}/moderation",
    response_model=LoungeModerationResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute Host moderation controls: mute, remove, lock, set_role (Host/Admin only)",
)
async def moderate_dj_lounge(
    payload: LoungeModerationRequest,
    id: str = Path(..., min_length=1, max_length=64),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LoungeModerationResponse:
    """Execute Host-only moderation actions on a DJ Lounge (REQ-025, REQ-070)."""
    return await moderate_lounge(
        db=db,
        lounge_id=id.strip(),
        current_user=current_user,
        payload=payload,
    )
