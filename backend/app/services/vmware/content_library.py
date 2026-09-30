"""vSphere Content Library discovery and OVF deployment over the REST API."""

from __future__ import annotations

import datetime as dt
import ssl
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from app.core.errors import InfraOperationError
from app.core.logging import get_logger
from app.schemas.infrastructure import TemplateOut
from app.secrets.service import SecretsService
from app.services.vmware.base import (
    CloneSpec,
    VCenterTarget,
    VmRef,
    ensure_tls_policy,
    owner_annotation,
    vcenter_ssl_context,
)

log = get_logger(__name__)

LIBRARY_ITEM_PREFIX = "library-item:"


def library_item_id(raw_id: str) -> str:
    """Return the Content Library id carried by a public template identifier."""
    if not raw_id.startswith(LIBRARY_ITEM_PREFIX):
        raise ValueError("Not a Content Library item identifier.")
    item_id = raw_id[len(LIBRARY_ITEM_PREFIX):]
    if not item_id:
        raise ValueError("Content Library item identifier is empty.")
    return item_id


def package_type(file_names: list[str]) -> str:
    """Classify only from actual package filenames; never infer OVA metadata."""
    lowered = [name.lower() for name in file_names]
    if any(name.endswith(".ova") for name in lowered):
        return "OVA"
    return "OVF"


def vapi_error_text(response: httpx.Response) -> str:
    """vCenter's own explanation of a failed REST call.

    The vSphere REST API returns ``{"error_type": ..., "messages": [...]}``;
    ``raise_for_status`` alone discards it, and it is the only place vCenter
    says which field or object it rejected.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text.strip()[:2000]
    if not isinstance(body, dict):
        return str(body)[:2000]
    messages = [
        str(message.get("default_message") or message.get("id"))
        for message in body.get("messages") or []
        if isinstance(message, dict) and (message.get("default_message") or message.get("id"))
    ]
    error_type = str(body.get("error_type") or "")
    text = "; ".join(messages) or str(body)
    return (f"{error_type}: {text}" if error_type else text)[:2000]


def _deployment_http_error(response: httpx.Response, vm_name: str) -> InfraOperationError:
    detail = f"HTTP {response.status_code} for {response.request.url}\n{vapi_error_text(response)}"
    if response.status_code in (401, 403):
        return InfraOperationError(
            f"vCenter refused the OVF/OVA deployment for '{vm_name}'.",
            reason="The vCenter account is not allowed to deploy from this Content Library item.",
            recommended_action=(
                "Grant the vCenter service account Content Library and virtual machine provisioning "
                "privileges on the library, resource pool, datastore, network and VM folder."
            ),
            technical_detail=detail,
            retryable=False,
        )
    if response.status_code < 500:
        return InfraOperationError(
            f"vCenter rejected the OVF/OVA deployment request for '{vm_name}'.",
            reason="vCenter did not accept the deployment specification or target.",
            recommended_action=(
                "An administrator can see vCenter's explanation in the technical output; check it "
                "against the selected package, resource pool, datastore and network, then retry."
            ),
            technical_detail=detail,
            retryable=False,
        )
    return InfraOperationError(
        f"vCenter could not deploy the OVF/OVA package for '{vm_name}'.",
        reason="The vSphere REST API returned a server error.",
        recommended_action="Check the vCenter task list and service health, then retry the stage.",
        technical_detail=detail,
        retryable=True,
    )


def _parse_datetime(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True)
class _LibraryInfo:
    id: str
    name: str


class ContentLibraryClient:
    """Small, dependency-free wrapper around the supported vSphere REST API."""

    def __init__(self, secrets: SecretsService) -> None:
        self._secrets = secrets

    @staticmethod
    def is_library_item(value: str) -> bool:
        return value.startswith(LIBRARY_ITEM_PREFIX)

    @asynccontextmanager
    async def _client(self, target: VCenterTarget):
        ensure_tls_policy(target)
        username, password = await self._secrets.get_credentials(
            target.username_secret_ref, target.password_secret_ref
        )
        verify: bool | ssl.SSLContext = vcenter_ssl_context(target)
        base_url = f"https://{target.host}:{target.port}/api"
        async with httpx.AsyncClient(base_url=base_url, verify=verify, timeout=60.0) as client:
            try:
                response = await client.post("/session", auth=(username, password))
                response.raise_for_status()
                token = response.json()
                if not isinstance(token, str) or not token:
                    raise ValueError("vCenter returned an invalid REST session token.")
                client.headers["vmware-api-session-id"] = token
                yield client
            except InfraOperationError:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                detail = f"{type(exc).__name__}: {exc}"
                if isinstance(exc, httpx.HTTPStatusError):
                    detail += f"\nvCenter response: {vapi_error_text(exc.response)}"
                raise InfraOperationError(
                    "Could not query the vCenter Content Library.",
                    reason="The vSphere REST API session or inventory request failed.",
                    recommended_action=(
                        "Verify Content Library read permissions and the vCenter TLS configuration."
                    ),
                    technical_detail=detail,
                    retryable=True,
                ) from exc
            finally:
                if client.headers.get("vmware-api-session-id"):
                    try:
                        await client.delete("/session")
                    except httpx.HTTPError:
                        pass

    @staticmethod
    async def _json(client: httpx.AsyncClient, path: str, **kwargs):
        response = await client.get(path, **kwargs)
        response.raise_for_status()
        return response.json()

    async def list_ovf_packages(
        self, target: VCenterTarget, datacenter_id: str | None = None
    ) -> list[TemplateOut]:
        """List actual OVF/OVA Content Library items.

        Content Libraries are vCenter-scoped, not datacenter-owned. The selected
        datacenter is therefore deliberately not copied into the response.
        Deployment-time target validation supplies the datacenter boundary.
        """
        del datacenter_id
        templates: list[TemplateOut] = []
        async with self._client(target) as client:
            library_ids = await self._json(client, "/content/library")
            for raw_library_id in library_ids if isinstance(library_ids, list) else []:
                if not isinstance(raw_library_id, str):
                    continue
                encoded_library_id = quote(raw_library_id, safe="")
                library_data = await self._json(client, f"/content/library/{encoded_library_id}")
                library = _LibraryInfo(
                    id=raw_library_id,
                    name=str(library_data.get("name") or raw_library_id),
                )
                item_ids = await self._json(
                    client,
                    "/content/library/item",
                    params={"library_id": library.id},
                )
                for raw_item_id in item_ids if isinstance(item_ids, list) else []:
                    if not isinstance(raw_item_id, str):
                        continue
                    encoded_item_id = quote(raw_item_id, safe="")
                    item = await self._json(client, f"/content/library/item/{encoded_item_id}")
                    if str(item.get("type") or "").lower() != "ovf":
                        continue
                    files = await self._json(
                        client, f"/content/library/item/{encoded_item_id}/file"
                    )
                    file_rows = files if isinstance(files, list) else []
                    names = [
                        str(row["name"])
                        for row in file_rows
                        if isinstance(row, dict) and row.get("name")
                    ]
                    size = item.get("size")
                    if not isinstance(size, int) or size <= 0:
                        measured = sum(
                            int(row.get("size") or 0)
                            for row in file_rows
                            if isinstance(row, dict)
                        )
                        size = measured or None
                    item_name = str(item.get("name") or raw_item_id)
                    templates.append(
                        TemplateOut(
                            id=f"{LIBRARY_ITEM_PREFIX}{raw_item_id}",
                            name=item_name,
                            type=package_type(names),
                            description=str(item.get("description") or ""),
                            datacenter_id=None,
                            datacenter_name=None,
                            # The item API identifies its library but does not
                            # expose a backing datastore name. Do not relabel
                            # the library as storage metadata.
                            storage_name=None,
                            location=f"Content Library / {library.name} / {item_name}",
                            size_bytes=size,
                            last_modified=_parse_datetime(item.get("last_modified_time")),
                        )
                    )
        return sorted(templates, key=lambda item: item.name.casefold())

    async def deploy_ovf_package(
        self,
        target: VCenterTarget,
        spec: CloneSpec,
        *,
        resource_pool_id: str,
    ) -> VmRef:
        item_id = library_item_id(spec.template_id)
        encoded_item_id = quote(item_id, safe="")
        deployment_target: dict[str, str] = {"resource_pool_id": resource_pool_id}
        if spec.host_id:
            deployment_target["host_id"] = spec.host_id

        async with self._client(target) as client:
            filter_response = await client.post(
                f"/vcenter/ovf/library-item/{encoded_item_id}",
                params={"action": "filter"},
                json={"target": deployment_target},
            )
            if filter_response.is_error:
                raise _deployment_http_error(filter_response, spec.vm_name)
            summary = filter_response.json()
            network_names = summary.get("networks") if isinstance(summary, dict) else []
            network_mappings = {
                str(name): spec.network_id
                for name in network_names or []
                if name and spec.network_id
            }

            deployment_spec: dict[str, object] = {
                "name": spec.vm_name,
                "annotation": owner_annotation(spec.description, spec.job_id),
                # A provisioning submission is an explicit deployment action.
                # vCenter still validates every EULA and returns structured errors.
                # Required, and the REST name is case-sensitive: the Python SDK
                # spelling ``accept_all_eula`` leaves it missing (HTTP 400).
                "accept_all_EULA": True,
            }
            if network_mappings:
                deployment_spec["network_mappings"] = network_mappings
            if spec.datastore_id:
                deployment_spec["default_datastore_id"] = spec.datastore_id

            response = await client.post(
                f"/vcenter/ovf/library-item/{encoded_item_id}",
                params={"action": "deploy"},
                headers={"Client-Token": str(uuid.uuid4())},
                json={"target": deployment_target, "deployment_spec": deployment_spec},
                timeout=1800.0,
            )
            if response.is_error:
                raise _deployment_http_error(response, spec.vm_name)
            result = response.json()
            resource = result.get("resource_id") if isinstance(result, dict) else None
            resource_id = resource.get("id") if isinstance(resource, dict) else None
            if not isinstance(result, dict) or not result.get("succeeded") or not resource_id:
                error = result.get("error") if isinstance(result, dict) else None
                raise InfraOperationError(
                    f"OVF/OVA deployment for '{spec.vm_name}' did not complete.",
                    reason="vCenter rejected the Content Library deployment specification.",
                    recommended_action=(
                        "Review the selected storage/network target and the package deployment requirements."
                    ),
                    technical_detail=str(error or result)[:6000],
                    retryable=False,
                )
            log.info(
                "vSphere OVF deployment complete item=%s vm=%s resource=%s",
                item_id,
                spec.vm_name,
                resource_id,
            )
            return VmRef(id=str(resource_id), name=spec.vm_name)
