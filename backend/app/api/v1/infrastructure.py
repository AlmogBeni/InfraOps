"""Infrastructure discovery endpoints backed by the VMware service layer."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import DbSession
from app.core.errors import NotFoundError
from app.models.infrastructure import VCenterConnection
from app.schemas.infrastructure import (
    ClusterOut,
    DatacenterOut,
    DatastoreClusterOut,
    DatastoreOut,
    HostOut,
    NetworkOut,
    ResourcePoolOut,
    TemplateOut,
    VCenterSummary,
)
from app.services.vmware.base import VCenterTarget
from app.services.vmware.factory import get_vmware_service

router = APIRouter(prefix="/infrastructure", tags=["infrastructure"])


async def _load_target(db, vcenter_id: uuid.UUID) -> VCenterTarget:
    row = await db.get(VCenterConnection, vcenter_id)
    if row is None:
        raise NotFoundError("vCenter connection not found.")
    if not row.enabled:
        raise NotFoundError(f"vCenter connection '{row.name}' is disabled.")
    return VCenterTarget(
        id=str(row.id), name=row.name, host=row.host, port=row.port,
        username_secret_ref=row.username_secret_ref,
        password_secret_ref=row.password_secret_ref,
        verify_ssl=row.verify_ssl,
    )


@router.get("/vcenters", response_model=list[VCenterSummary])
async def list_vcenters(db: DbSession) -> list[VCenterSummary]:
    result = await db.execute(select(VCenterConnection).order_by(VCenterConnection.name))
    return [
        VCenterSummary(
            id=str(row.id), name=row.name, host=row.host, port=row.port,
            enabled=row.enabled, connection_state=row.display_state,
            last_checked_at=row.last_checked_at,
        )
        for row in result.scalars().all()
    ]


@router.get("/vcenters/{vcenter_id}/datacenters", response_model=list[DatacenterOut])
async def list_datacenters(vcenter_id: uuid.UUID, db: DbSession) -> list[DatacenterOut]:
    target = await _load_target(db, vcenter_id)
    return await get_vmware_service().get_datacenters(target)


@router.get("/datacenters/{datacenter_id}/clusters", response_model=list[ClusterOut])
async def list_clusters(datacenter_id: str, vcenter_id: uuid.UUID, db: DbSession) -> list[ClusterOut]:
    target = await _load_target(db, vcenter_id)
    return await get_vmware_service().get_clusters(target, datacenter_id)


@router.get("/clusters/{cluster_id}/hosts", response_model=list[HostOut])
async def list_hosts(cluster_id: str, vcenter_id: uuid.UUID, db: DbSession) -> list[HostOut]:
    target = await _load_target(db, vcenter_id)
    return await get_vmware_service().get_hosts(target, cluster_id)


@router.get("/clusters/{cluster_id}/resource-pools", response_model=list[ResourcePoolOut])
async def list_resource_pools(cluster_id: str, vcenter_id: uuid.UUID, db: DbSession) -> list[ResourcePoolOut]:
    target = await _load_target(db, vcenter_id)
    return await get_vmware_service().get_resource_pools(target, cluster_id)


@router.get("/clusters/{cluster_id}/datastores", response_model=list[DatastoreOut])
async def list_datastores(cluster_id: str, vcenter_id: uuid.UUID, db: DbSession) -> list[DatastoreOut]:
    target = await _load_target(db, vcenter_id)
    return await get_vmware_service().get_datastores(target, cluster_id)


@router.get("/clusters/{cluster_id}/datastore-clusters", response_model=list[DatastoreClusterOut])
async def list_datastore_clusters(cluster_id: str, vcenter_id: uuid.UUID, db: DbSession) -> list[DatastoreClusterOut]:
    target = await _load_target(db, vcenter_id)
    return await get_vmware_service().get_datastore_clusters(target, cluster_id)


@router.get("/networks", response_model=list[NetworkOut])
async def list_networks(
    vcenter_id: uuid.UUID,
    db: DbSession,
    datacenter_id: str | None = Query(default=None),
) -> list[NetworkOut]:
    target = await _load_target(db, vcenter_id)
    return await get_vmware_service().get_networks(target, datacenter_id)


@router.get("/templates", response_model=list[TemplateOut])
async def list_templates(
    vcenter_id: uuid.UUID,
    db: DbSession,
    datacenter_id: str | None = Query(default=None),
) -> list[TemplateOut]:
    target = await _load_target(db, vcenter_id)
    return await get_vmware_service().get_templates(target, datacenter_id)
