from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class CloudRenderConfig:
    provider: str = "off"
    aws_region: str = "us-east-1"
    gcp_region: str = "us-central1"
    threshold_seconds: float = 300.0
    threshold_queue: int = 4

    @classmethod
    def from_env(cls) -> "CloudRenderConfig":
        provider = os.getenv("CLIPPER_CLOUD_RENDER", "off").strip().lower()
        if provider not in {"off", "lambda", "cloudrun"}:
            provider = "off"
        try:
            threshold_seconds = max(30.0, float(os.getenv("CLIPPER_CLOUD_RENDER_THRESHOLD_SECONDS", "300")))
        except ValueError:
            threshold_seconds = 300.0
        try:
            threshold_queue = max(1, int(os.getenv("CLIPPER_CLOUD_RENDER_THRESHOLD_QUEUE", "4")))
        except ValueError:
            threshold_queue = 4
        return cls(
            provider=provider,
            aws_region=os.getenv("REMOTION_AWS_REGION", "us-east-1"),
            gcp_region=os.getenv("REMOTION_GCP_REGION", "us-central1"),
            threshold_seconds=threshold_seconds,
            threshold_queue=threshold_queue,
        )

    @property
    def enabled(self) -> bool:
        return self.provider in {"lambda", "cloudrun"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "enabled": self.enabled,
            "awsRegion": self.aws_region,
            "gcpRegion": self.gcp_region,
            "thresholdSeconds": self.threshold_seconds,
            "thresholdQueue": self.threshold_queue,
        }


def should_burst(*, duration_seconds: float, queue_depth: int, config: CloudRenderConfig | None = None) -> bool:
    cfg = config or CloudRenderConfig.from_env()
    if not cfg.enabled:
        return False
    return float(duration_seconds) >= cfg.threshold_seconds or int(queue_depth) >= cfg.threshold_queue


def remotion_cloud_request(
    *,
    plan_url: str,
    clip_id: str,
    ratio: str,
    output_key: str,
    config: CloudRenderConfig | None = None,
) -> dict[str, Any]:
    """Return a provider-neutral request consumed by the Node render dispatcher.

    Credentials and actual Lambda/Cloud Run deployment remain environment concerns;
    keeping this request JSON-neutral means Firebase jobs can be retried or moved
    between local and cloud renderers without rewriting the edit plan.
    """
    cfg = config or CloudRenderConfig.from_env()
    if not cfg.enabled:
        raise RuntimeError("Cloud rendering is disabled")
    return {
        "version": 1,
        "provider": cfg.provider,
        "planUrl": plan_url,
        "clipId": clip_id,
        "ratio": ratio,
        "outputKey": output_key,
        "region": cfg.aws_region if cfg.provider == "lambda" else cfg.gcp_region,
    }
