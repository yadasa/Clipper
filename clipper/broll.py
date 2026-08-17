from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from .cache import file_fingerprint, stable_hash
from .config import Settings
from .models import VisualCue
from .visuals import generate_local_image, pull_commons_image

logger = logging.getLogger(__name__)

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm"}
_SUPPORTED_SUFFIXES = _IMAGE_SUFFIXES | _VIDEO_SUFFIXES
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'-]{2,}")
_STOPWORDS = {
    "about", "after", "again", "against", "also", "because", "before", "being", "between",
    "could", "does", "doing", "from", "have", "into", "just", "like", "more", "most",
    "other", "over", "really", "should", "some", "than", "that", "their", "them", "then",
    "there", "these", "they", "this", "those", "through", "very", "want", "what", "when",
    "where", "which", "while", "with", "would", "your",
}
_PEXELS_VIDEO_SEARCH = "https://api.pexels.com/v1/videos/search"
_PIXABAY_VIDEO_SEARCH = "https://pixabay.com/api/videos/"


class BrollError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class BrollAsset:
    path: Path
    media_type: str
    provider: str
    source_url: str | None = None
    attribution: dict[str, Any] = field(default_factory=dict)
    relevance_score: float = 0.0


class Provider(Protocol):
    name: str

    @property
    def available(self) -> bool: ...

    def resolve(self, cue: VisualCue, query: str, output_dir: Path) -> BrollAsset | None: ...


def _safe_slug(value: str, limit: int = 72) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-").lower()
    return (value[:limit] or "broll").rstrip("-")


def _tokens(value: str) -> list[str]:
    return [
        token.lower()
        for token in _TOKEN_RE.findall(value or "")
        if token.lower() not in _STOPWORDS
    ]


def _query_variants(cue: VisualCue) -> list[str]:
    candidates: list[str] = []
    primary = " ".join(str(cue.query or "").split()).strip()
    if primary:
        candidates.append(primary[:120])

    transcript_terms = _tokens(cue.transcript)
    if transcript_terms:
        compact = " ".join(dict.fromkeys(transcript_terms))[:120]
        if compact and compact.lower() not in {item.lower() for item in candidates}:
            candidates.append(compact)

    if not candidates:
        prompt_terms = _tokens(cue.prompt)
        if prompt_terms:
            candidates.append(" ".join(dict.fromkeys(prompt_terms))[:120])
    return candidates[:2]


def _lexical_relevance(query: str, candidate_text: str) -> float:
    query_tokens = set(_tokens(query))
    if not query_tokens:
        return 0.0
    candidate_tokens = set(_tokens(candidate_text))
    if not candidate_tokens:
        return 0.0
    overlap = len(query_tokens & candidate_tokens) / len(query_tokens)
    phrase_bonus = 0.15 if query.lower() in candidate_text.lower() else 0.0
    return max(0.0, min(1.0, overlap + phrase_bonus))


def _media_type(path: Path, explicit: str | None = None) -> str | None:
    if explicit in {"image", "video"}:
        return explicit
    suffix = path.suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        return "image"
    if suffix in _VIDEO_SUFFIXES:
        return "video"
    return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _asset_sidecar(path: Path) -> dict[str, Any]:
    for candidate in (path.with_suffix(path.suffix + ".json"), path.with_suffix(".json")):
        if candidate.is_file():
            data = _read_json(candidate)
            if data:
                return data
    return {}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(temp, path)


def _materialize(source: Path, output_dir: Path, filename: str | None = None) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / (filename or source.name)
    if target.is_file() and target.stat().st_size == source.stat().st_size:
        return target
    temp = target.with_name(target.name + ".part")
    temp.unlink(missing_ok=True)
    try:
        os.link(source, temp)
    except OSError:
        shutil.copy2(source, temp)
    os.replace(temp, target)
    return target


def _download(
    client: httpx.Client,
    url: str,
    destination: Path,
    *,
    max_bytes: int,
) -> Path:
    if destination.is_file() and destination.stat().st_size > 1024:
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".part")
    temp.unlink(missing_ok=True)
    total = 0
    try:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            content_length = int(response.headers.get("content-length") or 0)
            if content_length and content_length > max_bytes:
                raise BrollError(
                    f"B-roll asset is {content_length / (1024 * 1024):.1f} MB; "
                    f"limit is {max_bytes / (1024 * 1024):.0f} MB"
                )
            with temp.open("wb") as handle:
                for chunk in response.iter_bytes(1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise BrollError(
                            f"B-roll download exceeded {max_bytes / (1024 * 1024):.0f} MB"
                        )
                    handle.write(chunk)
        if total < 1024:
            raise BrollError("B-roll download was empty or unexpectedly small")
        os.replace(temp, destination)
        return destination
    except Exception:
        temp.unlink(missing_ok=True)
        raise


class _JsonTtlCache:
    def __init__(self, root: Path, ttl_seconds: int) -> None:
        self.root = root
        self.ttl_seconds = max(60, int(ttl_seconds))

    def _path(self, provider: str, query: str) -> Path:
        key = stable_hash({"provider": provider, "query": query})[:24]
        return self.root / provider / f"{key}.json"

    def get(self, provider: str, query: str) -> dict[str, Any] | None:
        path = self._path(provider, query)
        if not path.is_file():
            return None
        age = time.time() - path.stat().st_mtime
        if age > self.ttl_seconds:
            return None
        value = _read_json(path)
        return value or None

    def put(self, provider: str, query: str, payload: dict[str, Any]) -> None:
        _write_json_atomic(self._path(provider, query), payload)


class LocalLibraryProvider:
    name = "local"

    def __init__(self, settings: Settings) -> None:
        raw = settings.broll_library_path.strip()
        self.root = Path(raw).expanduser().resolve() if raw else None
        self._entries: list[tuple[Path, dict[str, Any], str]] | None = None

    @property
    def available(self) -> bool:
        return bool(self.root and self.root.is_dir())

    def _index(self) -> list[tuple[Path, dict[str, Any], str]]:
        if self._entries is not None:
            return self._entries
        entries: list[tuple[Path, dict[str, Any], str]] = []
        if not self.available or self.root is None:
            self._entries = entries
            return entries

        for path in self.root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in _SUPPORTED_SUFFIXES:
                continue
            metadata = _asset_sidecar(path)
            tags = metadata.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]
            searchable = " ".join(
                [
                    path.stem.replace("_", " ").replace("-", " "),
                    " ".join(path.parent.parts[-2:]),
                    str(metadata.get("title") or ""),
                    str(metadata.get("description") or ""),
                    " ".join(str(tag) for tag in tags),
                ]
            )
            entries.append((path, metadata, searchable))
        self._entries = entries
        return entries

    def resolve(self, cue: VisualCue, query: str, output_dir: Path) -> BrollAsset | None:
        del cue
        best: tuple[float, Path, dict[str, Any]] | None = None
        for path, metadata, searchable in self._index():
            score = _lexical_relevance(query, searchable)
            if best is None or score > best[0]:
                best = (score, path, metadata)
        if best is None or best[0] <= 0:
            return None

        score, path, metadata = best
        fingerprint = file_fingerprint(path, sample_bytes=256 * 1024)[:12]
        local = _materialize(path, output_dir, f"local-{fingerprint}{path.suffix.lower()}")
        attribution = {
            "source": "Local B-roll library",
            "creator": metadata.get("creator"),
            "license": metadata.get("license"),
            "source_url": metadata.get("source_url"),
            "original_path": str(path),
        }
        return BrollAsset(
            path=local,
            media_type=_media_type(local) or "image",
            provider=self.name,
            source_url=str(metadata.get("source_url") or "") or None,
            attribution=attribution,
            relevance_score=score,
        )


class PexelsVideoProvider:
    name = "pexels"

    def __init__(self, settings: Settings, client: httpx.Client, search_cache: _JsonTtlCache) -> None:
        self.settings = settings
        self.client = client
        self.search_cache = search_cache
        self.asset_cache = settings.workdir / "cache" / "broll" / "assets" / self.name

    @property
    def available(self) -> bool:
        return bool(self.settings.pexels_api_key)

    def _search(self, query: str) -> dict[str, Any]:
        cached = self.search_cache.get(self.name, query)
        if cached is not None:
            return cached
        response = self.client.get(
            _PEXELS_VIDEO_SEARCH,
            headers={"Authorization": self.settings.pexels_api_key},
            params={"query": query, "per_page": 10, "size": "medium"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise BrollError("Pexels returned an invalid response")
        self.search_cache.put(self.name, query, payload)
        return payload

    @staticmethod
    def _select_file(video: dict[str, Any]) -> dict[str, Any] | None:
        files = []
        for item in video.get("video_files") or []:
            if not isinstance(item, dict):
                continue
            link = str(item.get("link") or "")
            file_type = str(item.get("file_type") or "").lower()
            if not link or ("mp4" not in file_type and ".mp4" not in link.lower()):
                continue
            width = int(item.get("width") or 0)
            height = int(item.get("height") or 0)
            pixels = width * height
            if pixels <= 0:
                continue
            target = 1920 * 1080
            resolution_score = min(pixels, target) / target
            oversize_penalty = max(0, pixels - target) / (target * 8)
            files.append((resolution_score - oversize_penalty, item))
        if not files:
            return None
        files.sort(key=lambda pair: pair[0], reverse=True)
        return files[0][1]

    def resolve(self, cue: VisualCue, query: str, output_dir: Path) -> BrollAsset | None:
        payload = self._search(query)
        desired = max(1.0, cue.end - cue.start)
        videos = [item for item in payload.get("videos") or [] if isinstance(item, dict)]
        ranked: list[tuple[float, float, dict[str, Any], dict[str, Any]]] = []
        for index, video in enumerate(videos):
            chosen = self._select_file(video)
            if chosen is None:
                continue
            duration = float(video.get("duration") or 0)
            duration_score = 1.0 if duration >= desired else max(0.25, duration / desired)
            # Pexels does not expose textual tags for video hits. Its ordered
            # search rank is therefore semantic evidence; duration is only a
            # secondary fitness signal rather than a fake fixed relevance score.
            search_score = max(0.20, 0.82 - index * 0.06)
            relevance = search_score * 0.88 + duration_score * 0.12
            ranked.append((relevance, duration_score, video, chosen))
        if not ranked:
            return None
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        relevance, _, video, chosen = ranked[0]

        video_id = str(video.get("id") or stable_hash(video)[:10])
        file_id = str(chosen.get("id") or stable_hash(chosen)[:8])
        cached = self.asset_cache / f"{video_id}-{file_id}.mp4"
        _download(
            self.client,
            str(chosen["link"]),
            cached,
            max_bytes=self.settings.broll_max_download_mb * 1024 * 1024,
        )
        local = _materialize(cached, output_dir, f"pexels-{video_id}-{file_id}.mp4")
        user = video.get("user") if isinstance(video.get("user"), dict) else {}
        source_url = str(video.get("url") or "") or None
        attribution = {
            "source": "Pexels",
            "creator": user.get("name"),
            "creator_url": user.get("url"),
            "source_url": source_url,
            "search_query": query,
        }
        _write_json_atomic(local.with_suffix(local.suffix + ".json"), attribution)
        return BrollAsset(
            path=local,
            media_type="video",
            provider=self.name,
            source_url=source_url,
            attribution=attribution,
            relevance_score=max(0.0, min(1.0, relevance)),
        )


class PixabayVideoProvider:
    name = "pixabay"

    def __init__(self, settings: Settings, client: httpx.Client, search_cache: _JsonTtlCache) -> None:
        self.settings = settings
        self.client = client
        self.search_cache = search_cache
        self.asset_cache = settings.workdir / "cache" / "broll" / "assets" / self.name

    @property
    def available(self) -> bool:
        return bool(self.settings.pixabay_api_key)

    def _search(self, query: str) -> dict[str, Any]:
        cached = self.search_cache.get(self.name, query)
        if cached is not None:
            return cached
        response = self.client.get(
            _PIXABAY_VIDEO_SEARCH,
            params={
                "key": self.settings.pixabay_api_key,
                "q": query[:100],
                "per_page": 12,
                "safesearch": "true",
                "order": "popular",
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise BrollError("Pixabay returned an invalid response")
        self.search_cache.put(self.name, query, payload)
        return payload

    @staticmethod
    def _select_stream(hit: dict[str, Any]) -> dict[str, Any] | None:
        videos = hit.get("videos")
        if not isinstance(videos, dict):
            return None
        for name in ("medium", "small", "large", "tiny"):
            stream = videos.get(name)
            if isinstance(stream, dict) and stream.get("url"):
                return stream
        return None

    def resolve(self, cue: VisualCue, query: str, output_dir: Path) -> BrollAsset | None:
        payload = self._search(query)
        desired = max(1.0, cue.end - cue.start)
        hits = [hit for hit in payload.get("hits") or [] if isinstance(hit, dict)]
        ranked: list[tuple[float, float, dict[str, Any], dict[str, Any]]] = []
        for index, hit in enumerate(hits):
            stream = self._select_stream(hit)
            if stream is None:
                continue
            tags = str(hit.get("tags") or "")
            lexical = _lexical_relevance(query, tags)
            search_rank = max(0.0, 1.0 - index / max(1, len(hits)))
            relevance = lexical * 0.85 + search_rank * 0.15
            duration = float(hit.get("duration") or 0)
            duration_score = 1.0 if duration >= desired else max(0.25, duration / desired)
            ranked.append((relevance, duration_score, hit, stream))
        if not ranked:
            return None
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        relevance, _, hit, stream = ranked[0]

        video_id = str(hit.get("id") or stable_hash(hit)[:10])
        cached = self.asset_cache / f"{video_id}.mp4"
        _download(
            self.client,
            str(stream["url"]),
            cached,
            max_bytes=self.settings.broll_max_download_mb * 1024 * 1024,
        )
        local = _materialize(cached, output_dir, f"pixabay-{video_id}.mp4")
        source_url = str(hit.get("pageURL") or "") or None
        attribution = {
            "source": "Pixabay",
            "creator": hit.get("user"),
            "source_url": source_url,
            "license": "Pixabay Content License",
            "tags": hit.get("tags"),
            "search_query": query,
        }
        _write_json_atomic(local.with_suffix(local.suffix + ".json"), attribution)
        return BrollAsset(
            path=local,
            media_type="video",
            provider=self.name,
            source_url=source_url,
            attribution=attribution,
            relevance_score=max(0.0, min(1.0, relevance)),
        )


class CommonsImageProvider:
    name = "commons"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cache_root = settings.workdir / "cache" / "broll" / self.name

    @property
    def available(self) -> bool:
        return True

    def resolve(self, cue: VisualCue, query: str, output_dir: Path) -> BrollAsset | None:
        del cue
        query_root = self.cache_root / stable_hash(query)[:16]
        path, attribution = pull_commons_image(query, query_root)
        if path is None:
            return None
        local = _materialize(path, output_dir)
        source_url = str(attribution.get("description_url") or "") or None
        lexical = _lexical_relevance(query, str(attribution.get("page_title") or ""))
        # A first-page Commons search result contributes only weak evidence on
        # its own. At least some title/query agreement is needed to clear the
        # default relevance threshold.
        relevance = min(1.0, 0.15 + lexical * 0.85)
        attribution = dict(attribution)
        attribution["search_query"] = query
        return BrollAsset(
            path=local,
            media_type="image",
            provider=self.name,
            source_url=source_url,
            attribution=attribution,
            relevance_score=relevance,
        )


class DiffusersImageProvider:
    name = "diffusers"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cache_root = settings.workdir / "cache" / "broll" / self.name

    @property
    def available(self) -> bool:
        return bool(self.settings.diffusion_model)

    def resolve(self, cue: VisualCue, query: str, output_dir: Path) -> BrollAsset | None:
        del query
        prompt = cue.prompt or f"Editorial B-roll illustrating: {cue.transcript}"
        path = generate_local_image(prompt, self.cache_root, self.settings)
        local = _materialize(path, output_dir)
        return BrollAsset(
            path=local,
            media_type="image",
            provider=self.name,
            source_url=None,
            attribution={
                "source": "Local Diffusers generation",
                "model": self.settings.diffusion_model,
            },
            relevance_score=1.0,
        )


def _provider_order(settings: Settings) -> tuple[str, ...]:
    if settings.visual_provider == "none":
        return ()
    if settings.broll_providers:
        return settings.broll_providers

    legacy = settings.visual_provider.strip().lower()
    if legacy == "commons":
        return ("commons",)
    if legacy == "diffusers":
        return ("diffusers",)
    if legacy == "auto":
        return ("local", "pexels", "pixabay", "commons", "diffusers")
    return ("local", "pexels", "pixabay", "commons", "diffusers")


def _build_providers(settings: Settings, client: httpx.Client) -> list[Provider]:
    search_cache = _JsonTtlCache(
        settings.workdir / "cache" / "broll" / "search",
        settings.broll_search_cache_hours * 3600,
    )
    factories: dict[str, Any] = {
        "local": lambda: LocalLibraryProvider(settings),
        "pexels": lambda: PexelsVideoProvider(settings, client, search_cache),
        "pixabay": lambda: PixabayVideoProvider(settings, client, search_cache),
        "commons": lambda: CommonsImageProvider(settings),
        "diffusers": lambda: DiffusersImageProvider(settings),
    }
    providers: list[Provider] = []
    seen: set[str] = set()
    for name in _provider_order(settings):
        normalized = name.strip().lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        factory = factories.get(normalized)
        if factory is None:
            logger.warning("Ignoring unknown B-roll provider %r", name)
            continue
        provider = factory()
        if provider.available:
            providers.append(provider)
    return providers


def _apply_asset(cue: VisualCue, asset: BrollAsset) -> None:
    cue.asset_path = str(asset.path)
    cue.asset_type = asset.media_type
    cue.provider = asset.provider
    cue.source_url = asset.source_url
    cue.attribution = dict(asset.attribution)
    cue.relevance_score = round(float(asset.relevance_score), 4)


def _asset_identity(asset: BrollAsset) -> str:
    if asset.source_url:
        return "url:" + asset.source_url.strip().lower()
    try:
        return "file:" + file_fingerprint(asset.path, sample_bytes=256 * 1024)
    except OSError:
        return "path:" + str(asset.path.resolve()).lower()


def _cue_identity(cue: VisualCue) -> str | None:
    if cue.source_url:
        return "url:" + cue.source_url.strip().lower()
    if not cue.asset_path:
        return None
    path = Path(cue.asset_path).expanduser()
    if not path.is_file():
        return None
    try:
        return "file:" + file_fingerprint(path, sample_bytes=256 * 1024)
    except OSError:
        return "path:" + str(path.resolve()).lower()


def resolve_broll(
    cues: list[VisualCue],
    output_dir: str | Path,
    settings: Settings | None = None,
) -> list[VisualCue]:
    """Resolve planned transcript cues into project-local B-roll assets.

    Resolution is a deterministic waterfall: a creator's local library first,
    then configured stock providers, then Commons, then optional local generation.
    Manual cue assets are never overwritten. Remote search responses and files are
    cached outside the project; selected assets are hard-linked or copied into the
    project so rerenders remain self-contained. Exact asset reuse is avoided within
    a clip so neighboring automatic cues do not repeat the same cutaway.
    """
    settings = settings or Settings()
    if not settings.broll_auto_insert or not cues:
        return cues

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    timeout = httpx.Timeout(connect=10.0, read=45.0, write=45.0, pool=10.0)
    headers = {"User-Agent": "Clipper/0.2 local creator editor"}
    used_assets: set[str] = set()

    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        providers = _build_providers(settings, client)
        if not providers:
            return cues

        for index, cue in enumerate(cues):
            existing = Path(cue.asset_path).expanduser() if cue.asset_path else None
            if existing and existing.is_file():
                cue.asset_type = _media_type(existing, cue.asset_type)
                cue.provider = cue.provider or "manual"
                identity = _cue_identity(cue)
                if identity:
                    used_assets.add(identity)
                continue

            queries = _query_variants(cue)
            if not queries:
                continue
            cue_dir = output_root / f"cue_{index:02d}"

            selected: BrollAsset | None = None
            for provider in providers:
                for query in queries:
                    try:
                        candidate = provider.resolve(cue, query, cue_dir)
                    except Exception as exc:
                        logger.warning(
                            "B-roll provider %s failed for %r: %s",
                            provider.name,
                            query,
                            exc,
                        )
                        continue
                    if candidate is None:
                        continue
                    if candidate.relevance_score < settings.broll_min_relevance:
                        logger.info(
                            "Rejected %s B-roll for low relevance %.2f < %.2f",
                            provider.name,
                            candidate.relevance_score,
                            settings.broll_min_relevance,
                        )
                        continue
                    identity = _asset_identity(candidate)
                    if identity in used_assets:
                        logger.info("Rejected duplicate %s B-roll asset for cue %d", provider.name, index)
                        continue
                    selected = candidate
                    used_assets.add(identity)
                    break
                if selected is not None:
                    break

            if selected is not None:
                _apply_asset(cue, selected)

    return cues
