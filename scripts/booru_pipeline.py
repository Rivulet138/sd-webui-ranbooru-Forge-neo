"""Pure, Forge-independent online booru prompt generation helpers."""

from dataclasses import dataclass
import random


class RetryExhaustedError(RuntimeError):
    """Raised by a client after its bounded network retries are exhausted."""


@dataclass(frozen=True)
class CredentialInput:
    service: str = ""
    api_key: str = ""
    user_id: str = ""


@dataclass(frozen=True)
class BooruRequestConfig:
    service: str = ""
    tags: str = ""
    max_pages: int = 1
    post_id: str = ""
    mature_rating: str = "All"
    sorting_order: str = "Random"
    tag_categories: tuple = ()


@dataclass(frozen=True)
class PromptTransformConfig:
    remove_bad_tags: bool = False
    bad_tags: tuple = ()
    custom_remove: tuple = ()
    change_dash: bool = False
    shuffle_tags: bool = False
    seed: object = None
    limit_ratio: float = 1.0
    max_tags: int = 0
    background_tags: tuple = ()
    color_tags: tuple = ()
    mix_amount: int = 0


@dataclass(frozen=True)
class PromptGenerationResult:
    prompt: str = ""
    posts: tuple = ()
    selected_post: object = None
    error: str = ""
    error_kind: str = ""
    empty: bool = False


def resolve_credentials(service, supplied, saved):
    supplied = supplied or CredentialInput(service=service)
    service = str(service or "")
    if str(supplied.service or "") != service:
        supplied = CredentialInput(service=service)
    if supplied.api_key or supplied.user_id:
        return CredentialInput(service, supplied.api_key, supplied.user_id)
    saved_value = (saved or {}).get(service)
    if isinstance(saved_value, CredentialInput) and saved_value.service == service:
        return saved_value
    if isinstance(saved_value, dict):
        return CredentialInput(
            service,
            str(saved_value.get("api_key") or ""),
            str(saved_value.get("user_id") or ""),
        )
    return CredentialInput(service=service)


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _tags_for_post(post):
    value = post.get("tags") or post.get("tag_string") or ""
    if not value:
        fields = (
            "tag_string_general",
            "tag_string_artist",
            "tag_string_copyright",
            "tag_string_character",
            "tag_string_species",
            "tag_string_meta",
        )
        value = " ".join(str(post.get(field) or "") for field in fields)
    return tuple(token for token in str(value).replace(",", " ").split() if token)


def normalize_posts(service, payload):
    if isinstance(payload, dict):
        payload = payload.get("post", payload.get("posts"))
    if not isinstance(payload, list):
        raise ValueError(f"Unsupported {service} response shape")
    normalized = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        tags = _tags_for_post(item)
        if not tags:
            continue
        normalized.append(
            {
                "id": str(item.get("id") or ""),
                "tags": tags,
                "score": _safe_int(item.get("score"), 0),
                "rating": str(item.get("rating") or ""),
                "file_url": str(
                    item.get("file_url")
                    or item.get("large_file_url")
                    or item.get("preview_file_url")
                    or ""
                ),
                "preview_url": str(
                    item.get("preview_url")
                    or item.get("large_file_url")
                    or item.get("preview_file_url")
                    or ""
                ),
                "source_url": str(item.get("source") or item.get("source_url") or ""),
            }
        )
    return tuple(normalized)


def mix_post_tags(posts, mix_amount, rng):
    selected = []
    for _ in range(max(0, _safe_int(mix_amount))):
        if not posts:
            break
        selected.extend(posts[rng.randrange(len(posts))].get("tags", ()))
    return tuple(dict.fromkeys(selected))


def transform_prompt_tags(tags, config):
    values = [str(tag) for tag in tags if str(tag).strip()]
    removals = set(config.bad_tags if config.remove_bad_tags else ())
    removals.update(config.custom_remove)
    filtered = []
    for tag in values:
        if tag in removals:
            continue
        if any(rule.endswith("*") and rule[:-1] in tag for rule in removals):
            continue
        filtered.append(tag)
    rng = random.Random(config.seed) if config.seed is not None else random
    if config.shuffle_tags:
        rng.shuffle(filtered)
    if config.limit_ratio < 1:
        filtered = filtered[: max(0, int(len(filtered) * config.limit_ratio))]
    if config.max_tags > 0:
        filtered = filtered[: config.max_tags]
    if config.change_dash:
        filtered = [tag.replace("_", " ") for tag in filtered]
    return ", ".join((*config.background_tags, *config.color_tags, *filtered))


def _sort_posts(posts, sorting_order):
    if sorting_order == "High Score":
        return tuple(sorted(posts, key=lambda post: post["score"], reverse=True))
    if sorting_order == "Low Score":
        return tuple(sorted(posts, key=lambda post: post["score"]))
    return tuple(posts)


def generate_online_prompt(request, transform, credential, client_factory, rng=None):
    rng = rng or random.Random(transform.seed)
    client = None
    try:
        client = client_factory(request, credential)
        payload = client.fetch()
        posts = _sort_posts(normalize_posts(request.service, payload), request.sorting_order)
        if not posts:
            return PromptGenerationResult(posts=posts, empty=True)
        selected = posts[0] if request.sorting_order in ("High Score", "Low Score") else posts[rng.randrange(len(posts))]
        tags = selected["tags"]
        if transform.mix_amount:
            tags = mix_post_tags(posts, transform.mix_amount, rng)
        prompt = transform_prompt_tags(tags, transform)
        return PromptGenerationResult(prompt=prompt, posts=posts, selected_post=selected)
    except RetryExhaustedError as error:
        return PromptGenerationResult(error=str(error), error_kind="retry_exhausted")
    except ValueError as error:
        return PromptGenerationResult(error=str(error), error_kind="parse")
    except Exception as error:
        return PromptGenerationResult(error=str(error), error_kind="request")
    finally:
        if client is not None:
            client.close()
