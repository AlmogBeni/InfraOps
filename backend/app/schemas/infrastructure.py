"""Infrastructure discovery schemas returned by the VMware service layer."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict


class VCenterSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    host: str
    port: int
    enabled: bool
    connection_state: str
    last_checked_at: dt.datetime | None = None


class SiteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    vcenter_id: str
    enabled: bool


class DatacenterOut(BaseModel):
    id: str  # vSphere managed object reference
    name: str
    vcenter_id: str


class ClusterOut(BaseModel):
    id: str
    name: str
    datacenter_id: str
    drs_enabled: bool
    hosts_count: int = 0
    total_cpu_cores: int = 0
    total_memory_gb: float = 0


class HostOut(BaseModel):
    id: str
    name: str
    connection_state: str  # connected | disconnected | notResponding
    maintenance_mode: bool
    cpu_usage_percent: float
    memory_usage_percent: float
    available_for_provisioning: bool


class ResourcePoolOut(BaseModel):
    id: str
    name: str
    cluster_id: str


class DatastoreClusterOut(BaseModel):
    id: str
    name: str
    capacity_gb: float
    free_gb: float


class DatastoreOut(BaseModel):
    id: str
    name: str
    type: str  # VMFS | NFS | VSAN | VVOL
    capacity_gb: float
    free_gb: float
    usage_percent: float
    accessible: bool
    datastore_cluster_id: str | None = None


class NetworkOut(BaseModel):
    id: str
    name: str
    type: str  # STANDARD_PORT_GROUP | DISTRIBUTED_PORT_GROUP


class TemplateOut(BaseModel):
    id: str
    name: str
    os_family: str  # windows | linux | other
    os_version: str
    last_modified: dt.datetime | None = None
    description: str = ""


class ConnectionTestResult(BaseModel):
    ok: bool
    latency_ms: float | None = None
    detail: str = ""
