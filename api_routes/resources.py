"""Resource-domain API facade.

The legacy ``/api/assets`` endpoints remain backwards compatible in ``api.py``;
new clients can use this focused router for discovery and project-scoped asset
operations without depending on the monolithic module.
"""
from fastapi import APIRouter


def build_router(ctx) -> APIRouter:
    # The endpoint functions stay in api.py temporarily as compatibility
    # implementations, but registration is owned by this domain router.  This
    # keeps legacy URLs stable while making the resource boundary explicit.
    router = APIRouter(tags=["resources"])
    routes = [
        ("/api/comfy/status", "GET", "comfy_status_ep"),
        ("/api/comfy/start", "POST", "comfy_start_ep"),
        ("/api/comfy/stop", "POST", "comfy_stop_ep"),
        ("/api/comfy/templates", "GET", "comfy_templates_ep"),
        ("/api/comfy/model-check", "GET", "comfy_model_check_ep"),
        ("/api/comfy/templates/{template_id}", "GET", "comfy_template_ep"),
        ("/api/comfy/templates/apply", "POST", "comfy_template_apply_ep"),
        ("/api/comfy/provenance/validate", "POST", "comfy_provenance_validate_ep"),
        ("/api/comfy/jobs", "GET", "comfy_jobs_ep"),
        ("/api/comfy/retry/{prompt_id}", "POST", "comfy_retry_ep"),
        ("/api/comfy/queue", "POST", "comfy_queue_ep"),
        ("/api/comfy/history/{prompt_id}", "GET", "comfy_history_ep"),
        ("/api/comfy/wait/{prompt_id}", "GET", "comfy_wait_ep"),
        ("/api/comfy/watch/{prompt_id}", "POST", "comfy_watch_ep"),
        ("/api/comfy/watch/{prompt_id}", "GET", "comfy_watch_status_ep"),
        ("/api/comfy/cancel", "POST", "comfy_cancel_ep"),
        ("/api/comfy/import", "POST", "comfy_import_ep"),
        ("/api/comfy/import-all", "POST", "comfy_import_all_ep"),
        ("/api/comfy/resources/duplicates", "GET", "comfy_duplicates_ep"),
        ("/api/comfy/resources/unused", "GET", "comfy_unused_ep"),
        ("/api/assets/sources", "GET", "asset_sources_ep"),
        ("/api/assets/search", "GET", "asset_search_ep"),
        ("/api/assets/resolve", "GET", "asset_resolve_ep"),
        ("/api/assets/import", "POST", "asset_import_ep"),
        ("/api/assets/packs", "GET", "asset_packs_ep"),
        ("/api/assets/packs/peek", "POST", "asset_pack_peek_ep"),
        ("/api/assets/packs/import", "POST", "asset_pack_import_ep"),
        ("/api/assets/packs/preview", "GET", "asset_pack_preview_ep"),
        ("/api/assets/library", "GET", "asset_library_ep"),
        ("/api/assets/raw", "GET", "asset_raw_ep"),
        ("/api/assets/proxy", "GET", "asset_proxy_ep"),
        ("/api/assets/generate/status", "GET", "gen_status_ep"),
        ("/api/assets/generate/image", "POST", "gen_image_ep"),
        ("/api/assets/generate/animation", "POST", "gen_animation_ep"),
        ("/api/assets/generate/upload-frame", "POST", "gen_upload_frame_ep"),
        ("/api/assets/generate/jobs", "GET", "gen_jobs_ep"),
        ("/api/assets/generate/jobs/{job_id}", "GET", "gen_job_ep"),
        ("/api/assets/generate/jobs/{job_id}/cancel", "POST", "gen_job_cancel_ep"),
        ("/api/assets/cloud/providers", "GET", "cloud_providers_ep"),
        ("/api/assets/cloud/keys", "GET", "cloud_keys_ep"),
        ("/api/assets/cloud/key", "POST", "cloud_key_save_ep"),
        ("/api/assets/cloud/key/delete", "POST", "cloud_key_delete_ep"),
        ("/api/assets/cloud/image", "POST", "cloud_image_ep"),
        ("/api/assets/cloud/animation", "POST", "cloud_animation_ep"),
        ("/api/assets/cloud/upload-frame", "POST", "cloud_upload_frame_ep"),
        ("/api/asset_dependencies", "GET", "asset_deps_ep"),
        ("/api/preview_resource", "POST", "preview_ep"),
        ("/api/create_placeholder", "POST", "placeholder_ep"),
        ("/api/impact", "POST", "impact_ep"),
        ("/api/test_scene", "POST", "test_scene_ep"),
    ]
    for path, method, name in routes:
        router.add_api_route(path, getattr(ctx, name), methods=[method])
    return router


__all__ = ["build_router"]
