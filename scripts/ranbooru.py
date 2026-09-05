from io import BytesIO
import hashlib
import html
import random
import re
import requests
import modules.scripts as scripts
import gradio as gr
import os
from PIL import Image
import numpy as np
import importlib
import json
import threading
import sys
import uuid
try:
    import requests_cache
    HAS_REQUESTS_CACHE = True
except Exception:
    requests_cache = None
    HAS_REQUESTS_CACHE = False

from modules.processing import process_images, StableDiffusionProcessingImg2Img
from modules import shared
try:
    from modules.sd_hijack import model_hijack
except Exception:
    model_hijack = None
try:
    from modules import sd_models
except Exception:
    sd_models = None
try:
    from modules import deepbooru
except Exception:
    deepbooru = None
try:
    from modules.ui_components import InputAccordion
except Exception:
    InputAccordion = gr.Accordion

try:
    from .ranbooru_logging import get_logger, log_event, redact_sensitive
    from .version import USER_AGENT, __version__
    from .booru_pipeline import (
        BooruRequestConfig,
        CredentialInput,
        PromptTransformConfig,
        RetryExhaustedError,
        generate_online_prompt,
        resolve_credentials,
    )
except ImportError:
    import sys
    _ranbooru_scripts_dir = os.path.dirname(os.path.abspath(__file__))
    _added_ranbooru_scripts_dir = _ranbooru_scripts_dir not in sys.path
    if _added_ranbooru_scripts_dir:
        sys.path.insert(0, _ranbooru_scripts_dir)
    try:
        from ranbooru_logging import get_logger, log_event, redact_sensitive
        from version import USER_AGENT, __version__
        from booru_pipeline import (
            BooruRequestConfig,
            CredentialInput,
            PromptTransformConfig,
            RetryExhaustedError,
            generate_online_prompt,
            resolve_credentials,
        )
    finally:
        if _added_ranbooru_scripts_dir:
            sys.path.remove(_ranbooru_scripts_dir)


logger = get_logger()


def get_prompt_lengths(prompt):
    if model_hijack is not None and hasattr(model_hijack, "get_prompt_lengths"):
        return model_hijack.get_prompt_lengths(prompt)

    sd_model = getattr(shared, "sd_model", None)
    if sd_model is None and sd_models is not None:
        sd_model = getattr(getattr(sd_models, "model_data", None), "sd_model", None)

    get_lengths = getattr(sd_model, "get_prompt_lengths_on_ui", None)
    if get_lengths is not None:
        return get_lengths(prompt)

    token_count = len(str(prompt).strip("!,. ").replace(" ", ",").replace(".", ",").replace("!", ",").split(","))
    return token_count, max(75, ((max(token_count, 1) + 74) // 75) * 75)


def has_deepbooru():
    return deepbooru is not None and getattr(deepbooru, "model", None) is not None



extension_root = scripts.basedir()
user_data_dir = os.path.join(extension_root, 'user')
user_search_dir = os.path.join(user_data_dir, 'search')
user_remove_dir = os.path.join(user_data_dir, 'remove')
user_credentials_dir = os.path.join(user_data_dir, 'credentials')
user_cache_dir = os.path.join(user_data_dir, 'cache')
os.makedirs(user_search_dir, exist_ok=True)
os.makedirs(user_remove_dir, exist_ok=True)
os.makedirs(user_credentials_dir, exist_ok=True)
os.makedirs(user_cache_dir, exist_ok=True)

# ─── Global constant: default bad tags (with underscore and space variants) ───
DEFAULT_BAD_TAGS = [
    # underscore variants
    'mixed-language_text', 'watermark', 'text', 'english_text', 'speech_bubble',
    'signature', 'artist_name', 'censored', 'bar_censor', 'translation',
    'twitter_username', 'twitter_logo', 'patreon_username', 'commentary_request',
    'tagme', 'commentary', 'character_name', 'mosaic_censoring', 'instagram_username',
    'text_focus', 'english_commentary', 'comic', 'translation_request', 'fake_text',
    'translated', 'paid_reward_available', 'thought_bubble', 'multiple_views',
    'silent_comic', 'out-of-frame_censoring', 'symbol-only_commentary', '3koma',
    '2koma', 'character_watermark', 'spoken_question_mark', 'japanese_text',
    'spanish_text', 'language_text', 'fanbox_username', 'commission', 'original',
    'ai_generated', 'stable_diffusion', 'tagme_(artist)', 'text_bubble', 'qr_code',
    'chinese_commentary', 'korean_text', 'partial_commentary', 'chinese_text',
    'copyright_request', 'heart_censor', 'censored_nipples', 'page_number', 'scan',
    'fake_magazine_cover', 'korean_commentary',
    'sample_watermark', 'copyright_notice', 'copyright_name', 'album_cover', 'company_name',
    # space variants
    'mixed language text', 'english text', 'speech bubble', 'artist name', 'bar censor',
    'twitter username', 'twitter logo', 'patreon username', 'commentary request',
    'character name', 'mosaic censoring', 'instagram username', 'text focus',
    'english commentary', 'translation request', 'fake text', 'thought bubble',
    'multiple views', 'silent comic', 'out of frame censoring',
    'symbol only commentary', 'character watermark', 'spoken question mark',
    'japanese text', 'spanish text', 'language text', 'fanbox username',
    'ai generated', 'stable diffusion', 'tagme (artist)', 'text bubble',
    'chinese commentary', 'korean text', 'partial commentary', 'chinese text',
    'copyright request', 'heart censor', 'censored nipples', 'page number',
    'fake magazine cover', 'korean commentary',
    'sample watermark', 'copyright notice', 'copyright name', 'album cover', 'company name',
]

# ─── Tag cache manager ───────────────────────────────────────────────────────
try:
    from .cache_db import TagCacheManager
except ImportError:
    import sys
    _ranbooru_scripts_dir = os.path.dirname(os.path.abspath(__file__))
    _added_ranbooru_scripts_dir = _ranbooru_scripts_dir not in sys.path
    if _added_ranbooru_scripts_dir:
        sys.path.insert(0, _ranbooru_scripts_dir)
    try:
        from cache_db import TagCacheManager
    finally:
        if _added_ranbooru_scripts_dir:
            sys.path.remove(_ranbooru_scripts_dir)

try:
    from .natural_language import (
        BACKEND_CHOICES as NATURAL_LANGUAGE_BACKENDS,
        BACKEND_OFF as NATURAL_LANGUAGE_OFF,
        ENDPOINT_POLICY_CHOICES as NATURAL_LANGUAGE_ENDPOINT_POLICIES,
        ENDPOINT_POLICY_UNRESTRICTED as NATURAL_LANGUAGE_ENDPOINT_UNRESTRICTED,
        CONVERTER_VERSION as NATURAL_LANGUAGE_CONVERTER_VERSION,
        PRESET_CHOICES as NATURAL_LANGUAGE_PRESETS,
        PRESET_KREA2,
        CachedTagNaturalLanguageConverter,
        NaturalLanguageBatchCancelled,
        NaturalLanguageConfig,
        is_retryable_conversion_error,
        is_timeout_conversion_error,
        iter_cached_tag_conversions,
    )
except ImportError:
    import sys
    _ranbooru_scripts_dir = os.path.dirname(os.path.abspath(__file__))
    _added_ranbooru_scripts_dir = _ranbooru_scripts_dir not in sys.path
    if _added_ranbooru_scripts_dir:
        sys.path.insert(0, _ranbooru_scripts_dir)
    try:
        from natural_language import (
            BACKEND_CHOICES as NATURAL_LANGUAGE_BACKENDS,
            BACKEND_OFF as NATURAL_LANGUAGE_OFF,
            ENDPOINT_POLICY_CHOICES as NATURAL_LANGUAGE_ENDPOINT_POLICIES,
            ENDPOINT_POLICY_UNRESTRICTED as NATURAL_LANGUAGE_ENDPOINT_UNRESTRICTED,
            CONVERTER_VERSION as NATURAL_LANGUAGE_CONVERTER_VERSION,
            PRESET_CHOICES as NATURAL_LANGUAGE_PRESETS,
            PRESET_KREA2,
            CachedTagNaturalLanguageConverter,
            NaturalLanguageBatchCancelled,
            NaturalLanguageConfig,
            is_retryable_conversion_error,
            is_timeout_conversion_error,
            iter_cached_tag_conversions,
        )
    finally:
        if _added_ranbooru_scripts_dir:
            sys.path.remove(_ranbooru_scripts_dir)


tag_cache_manager = TagCacheManager(user_cache_dir)
_natural_batch_cancel = threading.Event()
_natural_batch_cancel_events: dict[str, threading.Event] = {}
_natural_batch_cancel_lock = threading.Lock()


def _natural_cancel_event(cancel_id: str = "") -> tuple[str, threading.Event]:
    key = str(cancel_id or "").strip() or f"direct:{threading.get_ident()}"
    with _natural_batch_cancel_lock:
        return key, _natural_batch_cancel_events.setdefault(key, threading.Event())
_natural_endpoint_policy_choices = list(NATURAL_LANGUAGE_ENDPOINT_POLICIES)
_prompt_studio_module = None
_prompt_studio_lock = threading.RLock()


def _load_prompt_studio_module():
    global _prompt_studio_module
    with _prompt_studio_lock:
        if _prompt_studio_module is not None:
            return _prompt_studio_module
        loaded = sys.modules.get("prompt_studio_ui")
        if loaded is not None and hasattr(loaded, "receive_ranbooru_handoff"):
            _prompt_studio_module = loaded
            return loaded
        studio_scripts = os.path.join(
            os.path.dirname(extension_root),
            "sd-webui-llm-prompt-studio",
            "scripts",
        )
        studio_ui = os.path.join(studio_scripts, "prompt_studio_ui.py")
        if not os.path.isfile(studio_ui):
            raise RuntimeError("未安装 sd-webui-llm-prompt-studio，无法进行实时联动")
        added_path = studio_scripts not in sys.path
        if added_path:
            sys.path.insert(0, studio_scripts)
        try:
            module = importlib.import_module("prompt_studio_ui")
            if not hasattr(module, "receive_ranbooru_handoff"):
                raise RuntimeError("LLM 提示词工作室缺少实时联动接口")
            _prompt_studio_module = module
            return module
        finally:
            if added_path:
                sys.path.remove(studio_scripts)


def _prompt_studio_payload(current_prompt, record_id=""):
    prompt = str(current_prompt or "").strip()
    if not prompt:
        raise ValueError("请先从 Ranbooru 缓存取出一条 Prompt")
    try:
        stable_id = int(float(record_id)) if str(record_id or "").strip() else None
    except (TypeError, ValueError, OverflowError):
        stable_id = None
    record = tag_cache_manager.get_record_by_id(stable_id) if stable_id is not None else None
    if record is None:
        record = tag_cache_manager.find_record_by_prompt(prompt)
    database_path = os.path.normcase(os.path.abspath(tag_cache_manager.db_path))
    database_key = hashlib.sha256(database_path.encode("utf-8")).hexdigest()[:16]
    if record is None:
        return {
            "database_key": database_key,
            "selected_prompt": prompt,
            "selected_is_natural": False,
            "tags_prompt": prompt,
        }
    natural_prompt = str(record.get("natural_prompt") or "").strip()
    return {
        "ranbooru_id": record.get("id", ""),
        "database_key": database_key,
        "tags_prompt": str(record.get("tags_prompt") or "").strip(),
        "natural_prompt": natural_prompt,
        "selected_prompt": prompt,
        "selected_is_natural": bool(natural_prompt and prompt == natural_prompt),
        "rating": record.get("rating", ""),
        "source_score": record.get("score", 0),
        "booru": record.get("booru", ""),
        "post_id": record.get("post_id", ""),
        "source_url": record.get("source_url", ""),
    }


def _cache_send_to_prompt_studio(current_prompt, record_id=""):
    try:
        result = _load_prompt_studio_module().receive_ranbooru_handoff(
            _prompt_studio_payload(current_prompt, record_id),
            "send",
        )
        return result["status"]
    except Exception as error:
        return f"发送失败：{error}"


def _cache_process_with_prompt_studio(current_prompt, record_id=""):
    try:
        result = _load_prompt_studio_module().process_ranbooru_handoff(
            _prompt_studio_payload(current_prompt, record_id)
        )
        return result["prompt"], result["status"]
    except Exception as error:
        return "", f"处理失败：{error}"


class CredentialReadError(RuntimeError):
    """Raised when an existing credential store cannot be read safely."""


# Initialize credentials manager
class CredentialsManager:
    def __init__(self, extension_root):
        self.extension_root = extension_root
        self.credentials_dir = os.path.join(extension_root, 'user', 'credentials')
        self.credentials_file = os.path.join(self.credentials_dir, 'credentials.json')
        self._lock = threading.RLock()

        # Create credentials directory if it doesn't exist
        os.makedirs(self.credentials_dir, exist_ok=True)

        # Initialize credentials file if it doesn't exist
        if not os.path.exists(self.credentials_file):
            self._save_credentials({})

    def _load_credentials(self, for_write=False):
        """Load credentials from the JSON file"""
        with self._lock:
            try:
                with open(self.credentials_file, 'r', encoding='utf-8') as f:
                    payload = json.load(f)
                if not isinstance(payload, dict):
                    raise CredentialReadError("Credential store must contain a JSON object")
                return payload
            except FileNotFoundError:
                return {}
            except (json.JSONDecodeError, OSError, CredentialReadError) as error:
                log_event(
                    logger,
                    "credential_read_failure",
                    "Could not read credential store: %s",
                    error,
                )
                if for_write:
                    raise CredentialReadError(
                        "Existing credential store could not be read; refusing to overwrite it"
                    ) from error
                return {}

    def _save_credentials(self, credentials):
        """Save credentials to the JSON file"""
        with self._lock:
            temp_file = f"{self.credentials_file}.tmp"
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(credentials, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.chmod(temp_file, 0o600)
            except OSError:
                pass
            os.replace(temp_file, self.credentials_file)
            try:
                os.chmod(self.credentials_file, 0o600)
            except OSError:
                pass

    def save_booru_credentials(self, booru_name, api_key, user_id=None):
        """Save API credentials for a specific booru"""
        with self._lock:
            credentials = self._load_credentials(for_write=True)
            if booru_name not in credentials:
                credentials[booru_name] = {}
            credentials[booru_name]['api_key'] = api_key
            if user_id is not None:
                credentials[booru_name]['user_id'] = user_id
            self._save_credentials(credentials)

    def get_booru_credentials(self, booru_name):
        """Get API credentials for a specific booru"""
        credentials = self._load_credentials()
        return credentials.get(booru_name, {})

    def has_credentials(self, booru_name):
        """Check if credentials exist for a specific booru"""
        credentials = self.get_booru_credentials(booru_name)
        return 'api_key' in credentials and credentials['api_key'].strip() != ''

    def clear_booru_credentials(self, booru_name):
        """Clear credentials for a specific booru"""
        with self._lock:
            credentials = self._load_credentials(for_write=True)
            if booru_name in credentials:
                del credentials[booru_name]
                self._save_credentials(credentials)

    def save_natural_language_settings(
        self,
        preset,
        backend,
        endpoint_policy,
        endpoint,
        model,
        api_key,
        timeout,
        rag_enabled=False,
        rag_top_k=3,
        rag_min_percentile=75,
        rag_context_chars=3000,
    ):
        """Persist the complete LLM conversion configuration, including API Key."""
        try:
            timeout_value = min(300.0, max(1.0, float(timeout or 120)))
        except (TypeError, ValueError):
            timeout_value = 120.0
        try:
            rag_top_k_value = max(1, min(5, int(float(rag_top_k or 3))))
        except (TypeError, ValueError, OverflowError):
            rag_top_k_value = 3
        try:
            rag_min_percentile_value = max(
                0.0,
                min(100.0, float(rag_min_percentile)),
            )
        except (TypeError, ValueError, OverflowError):
            rag_min_percentile_value = 75.0
        try:
            rag_context_chars_value = max(
                0,
                min(12000, int(float(rag_context_chars or 0))),
            )
        except (TypeError, ValueError, OverflowError):
            rag_context_chars_value = 3000
        if isinstance(rag_enabled, str):
            rag_enabled_value = rag_enabled.strip().lower() not in {
                "0", "false", "no", "off"
            }
        else:
            rag_enabled_value = bool(rag_enabled)
        backend_value = str(backend or NATURAL_LANGUAGE_OFF)
        endpoint_value = str(endpoint or "").strip()
        with self._lock:
            credentials = self._load_credentials(for_write=True)
            existing = credentials.get("natural_language", {})
            same_service = (
                isinstance(existing, dict)
                and str(existing.get("backend") or NATURAL_LANGUAGE_OFF) == backend_value
                and str(existing.get("endpoint") or "").strip() == endpoint_value
            )
            existing_key = str(existing.get("api_key") or "").strip() if same_service else ""
            settings = {
                "preset": str(preset or PRESET_KREA2),
                "backend": backend_value,
                "endpoint_policy": str(
                    endpoint_policy or NATURAL_LANGUAGE_ENDPOINT_UNRESTRICTED
                ),
                "endpoint": endpoint_value,
                "model": str(model or "").strip(),
                "api_key": str(api_key or "").strip() or existing_key,
                "timeout": timeout_value,
                "rag_enabled": rag_enabled_value,
                "rag_top_k": rag_top_k_value,
                "rag_min_percentile": rag_min_percentile_value,
                "rag_context_chars": rag_context_chars_value,
            }
            credentials["natural_language"] = settings
            self._save_credentials(credentials)
        return settings

    def get_natural_language_settings(self):
        settings = self._load_credentials().get("natural_language", {})
        return dict(settings) if isinstance(settings, dict) else {}

    def clear_natural_language_settings(self):
        with self._lock:
            credentials = self._load_credentials(for_write=True)
            credentials.pop("natural_language", None)
            self._save_credentials(credentials)

credentials_manager = CredentialsManager(extension_root)

if not os.path.isfile(os.path.join(user_search_dir, 'tags_search.txt')):
    with open(os.path.join(user_search_dir, 'tags_search.txt'), 'w'):
        pass
if not os.path.isfile(os.path.join(user_remove_dir, 'tags_remove.txt')):
    with open(os.path.join(user_remove_dir, 'tags_remove.txt'), 'w'):
        pass

COLORED_BG = ['black_background', 'aqua_background', 'white_background', 'colored_background', 'gray_background', 'blue_background', 'green_background', 'red_background', 'brown_background', 'purple_background', 'yellow_background', 'orange_background', 'pink_background', 'plain', 'transparent_background', 'simple_background', 'two-tone_background', 'grey_background']
ADD_BG = ['outdoors', 'indoors']
BW_BG = ['monochrome', 'greyscale', 'grayscale']
POST_AMOUNT = 100
DEBUG = False
RATING_TYPES = {
    "none": {
        "All": "All"
    },
    "full": {
        "All": "All",
        "Safe": "safe",
        "Questionable": "questionable",
        "Explicit": "explicit"
    },
    "single": {
        "All": "All",
        "Safe": "g",
        "Sensitive": "s",
        "Questionable": "q",
        "Explicit": "e"
    }
}
RATINGS = {
    "e621": RATING_TYPES['full'],
    "danbooru": RATING_TYPES['single'],
    "aibooru": RATING_TYPES['full'],
    "yande.re": RATING_TYPES['full'],
    "konachan": RATING_TYPES['full'],
    "safebooru": RATING_TYPES['none'],
    "rule34": RATING_TYPES['full'],
    "xbooru": RATING_TYPES['full'],
    "gelbooru": RATING_TYPES['single']
}


def get_available_ratings(booru):
    mature_ratings = gr.update(choices=list(RATINGS[booru].keys()), value="All")
    return mature_ratings


def show_fringe_benefits(booru):
    if booru == 'gelbooru':
        return gr.update(visible=True, value=True)
    if booru == 'rule34':
        return gr.update(visible=True, value=False)
    return gr.update(visible=False, value=False)


def check_exception(booru, parameters):
    post_id = parameters.get('post_id')
    if booru == 'konachan' and post_id:
        raise ValueError("Konachan does not support post IDs")
    if booru == 'yande.re' and post_id:
        raise ValueError("Yande.re does not support post IDs")


def _response_posts_or_raise(response, source, keys=("posts", "post")):
    try:
        payload = response.json()
    except ValueError as error:
        raise RuntimeError(f"{source} 返回了无法解析的 JSON") from error
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            if key in payload:
                posts = payload[key]
                if isinstance(posts, list):
                    return posts
                raise RuntimeError(f"{source} 的 {key} 字段不是列表")
    raise RuntimeError(f"{source} 返回了不支持的 JSON 结构")


class Booru():

    def __init__(self, booru, booru_url):
        self.booru = booru
        self.base_url = booru_url
        self.booru_url = booru_url
        self.headers = {'user-agent': USER_AGENT}
        self.session = requests.Session()

    def configure_http_cache(self, enabled=False):
        """Use a client-local cache without patching process-wide requests."""
        try:
            self.session.close()
        except Exception as error:
            # requests and requests-cache expose third-party session implementations.
            logger.debug("Previous HTTP session close failed: %s", error)
        if enabled and HAS_REQUESTS_CACHE:
            cache_path = os.path.join(user_cache_dir, "ranbooru_http_cache")
            self.session = requests_cache.CachedSession(
                cache_name=cache_path,
                backend='sqlite',
                expire_after=3600,
            )
        else:
            self.session = requests.Session()
        return self

    def close(self):
        try:
            self.session.close()
        except Exception as error:
            # Object finalization must tolerate partially initialized host sessions.
            logger.debug("HTTP session close failed: %s", error)

    def __del__(self):
        self.close()

    def fetch_with_retry(self, url, max_retries=3, timeout=10, **kwargs):
        """Fetch URL with retry logic for rate limiting and network errors."""
        import time
        safe_url = re.sub(r'((?:api_key|user_id)=)[^&\s]+', r'\1***', str(url))
        for attempt in range(max_retries):
            try:
                response = self.session.get(url, timeout=timeout, **kwargs)
                if response.status_code == 429:
                    wait = 2 ** attempt
                    log_event(logger, "network_retry", "%s rate limited; retrying in %ss", self.booru, wait)
                    time.sleep(wait)
                    continue
                response.raise_for_status()
                return response
            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    log_event(
                        logger,
                        "network_timeout",
                        "%s request timed out; retry %s/%s",
                        self.booru,
                        attempt + 1,
                        max_retries,
                    )
                    time.sleep(2 ** attempt)
            except requests.exceptions.RequestException as e:
                safe_error = redact_sensitive(e)
                if attempt < max_retries - 1:
                    log_event(
                        logger,
                        "network_retry",
                        "%s request failed: %s; retry %s/%s",
                        self.booru,
                        safe_error,
                        attempt + 1,
                        max_retries,
                    )
                    time.sleep(2 ** attempt)
        raise Exception(f"[{self.booru}] All {max_retries} attempts failed for {safe_url}")

    def get_data(self, add_tags, max_pages=10, id=''):
        pass

    def get_post(self, add_tags, max_pages=10, id=''):
        return self.get_data(add_tags, max_pages, "&id=" + id)

    def _filter_tags_by_category(self, post, categories):
        if not categories or not isinstance(categories, list):
            return post.get('tag_string', '')
        parts = []
        for category in categories:
            field = f'tag_string_{category}'
            if field in post:
                parts.append(post[field])
        return ' '.join(parts) if parts else post.get('tag_string', '')


class Gelbooru(Booru):

    def __init__(self, fringe_benefits=True, api_key=None, user_id=None):
        super().__init__('gelbooru', f'https://gelbooru.com/index.php?page=dapi&s=post&q=index&json=1&limit={POST_AMOUNT}')
        self.fringe_benefits = fringe_benefits
        self.api_key = api_key
        self.user_id = user_id

    def get_data(self, add_tags, max_pages=10, id=''):
        max_pages = _normalize_max_pages(max_pages)
        for attempt in range(2):
            local_add_tags = '' if id else add_tags
            url = f"{self.base_url}&pid={random.randint(0, max_pages-1)}{id}{local_add_tags}"
            if self.api_key and self.user_id:
                url += f"&api_key={self.api_key}&user_id={self.user_id}"
            if self.fringe_benefits:
                url += "&fringeBenefits=1"
            self.booru_url = url
            res = self.fetch_with_retry(url, timeout=10)
            data = _response_posts_or_raise(res, "Gelbooru")
            result_count = len(data)
            if result_count == 0:
                max_pages = 2
                if attempt == 0:
                    logger.debug("Processing %s results", result_count)
                continue
            break
        for post in data:
            if isinstance(post, dict) and 'directory' in post and 'image' in post:
                post['file_url'] = f"https://img3.gelbooru.com/images/{post['directory']}/{post['image']}"
        return {'post': data}

    def get_data_page(self, add_tags, page=0, id=''):
        local_add_tags = '' if id else add_tags
        url = f"{self.base_url}&pid={page}{id}{local_add_tags}"
        if self.api_key and self.user_id:
            url += f"&api_key={self.api_key}&user_id={self.user_id}"
        if self.fringe_benefits:
            url += "&fringeBenefits=1"
        self.booru_url = url
        res = self.fetch_with_retry(url, timeout=10)
        data = _response_posts_or_raise(res, "Gelbooru page")
        for post in data:
            if isinstance(post, dict) and 'directory' in post and 'image' in post:
                post['file_url'] = f"https://img3.gelbooru.com/images/{post['directory']}/{post['image']}"
        return {'post': data}

class e621(Booru):

    def __init__(self):
        super().__init__('e621', f'https://e621.net/posts.json?limit={POST_AMOUNT}')

    def get_data(self, add_tags, max_pages=10, id='', tag_categories=None):
        max_pages = _normalize_max_pages(max_pages)
        for attempt in range(2):
            if id:
                add_tags = ''
            random_page = random.randint(1, max(1, int(max_pages)))
            url = f"{self.base_url}&page={random_page}{add_tags}"
            self.booru_url = url
            res = self.fetch_with_retry(url, headers=self.headers, timeout=10)
            posts = _response_posts_or_raise(res, "e621", keys=("posts",))
            for post in posts:
                if isinstance(post, dict):
                    post['tags'] = self._filter_tags_by_category(post, tag_categories)
            result_count = len(posts)
            if result_count == 0:
                max_pages = 2
                if attempt == 0:
                    logger.debug("Processing %s results", result_count)
                continue
            else:
                logger.debug("Found enough results")
            break
        return {'post': posts}

    def get_data_page(self, add_tags, page=0, id='', tag_categories=None):
        if id:
            add_tags = ''
        safe_page = max(1, int(page) + 1)
        url = f"{self.base_url}&page={safe_page}{add_tags}"
        self.booru_url = url
        res = self.fetch_with_retry(url, headers=self.headers, timeout=10)
        posts = _response_posts_or_raise(res, "e621 page", keys=("posts",))
        for post in posts:
            if isinstance(post, dict):
                post['tags'] = self._filter_tags_by_category(post, tag_categories)
        return {'post': posts}

    def _filter_tags_by_category(self, post, categories):
        """Filter tags by selected categories (e621 uses nested tags dict)."""
        if not categories or not isinstance(categories, list):
            tags_dict = post.get('tags', {})
            if isinstance(tags_dict, dict):
                all_tags = []
                for cat in ['general', 'artist', 'copyright', 'character', 'species', 'meta']:
                    all_tags.extend(tags_dict.get(cat, []))
                return ' '.join(all_tags)
            return ''
        tags_dict = post.get('tags', {})
        if not isinstance(tags_dict, dict):
            return ''
        parts = []
        for cat in categories:
            if cat in tags_dict:
                parts.extend(tags_dict[cat])
        return ' '.join(parts) if parts else ''

    def get_post(self, add_tags, max_pages=10, id=''):
        if not id:
            return self.get_data(add_tags, max_pages, '', tag_categories=None)
        self.booru_url = f"https://e621.net/posts/{id}.json"
        res = self.fetch_with_retry(self.booru_url, headers=self.headers, timeout=10)
        data = res.json()
        if isinstance(data, dict):
            post = data.get('post', data)
            if isinstance(post, dict):
                post['tags'] = self._filter_tags_by_category(post, None)
                return {'post': [post]}
        return {'post': []}



class XBooru(Booru):

    def __init__(self):
        super().__init__('xbooru', f'https://xbooru.com/index.php?page=dapi&s=post&q=index&json=1&limit={POST_AMOUNT}')

    def get_data(self, add_tags, max_pages=10, id=''):
        max_pages = _normalize_max_pages(max_pages)
        for attempt in range(2):
            if id:
                add_tags = ''
            url = f"{self.base_url}&pid={random.randint(0, max_pages-1)}{id}{add_tags}"
            self.booru_url = url
            logger.debug("Requesting booru URL: %s", redact_sensitive(str(url)))
            res = self.fetch_with_retry(url, timeout=10)
            data = _response_posts_or_raise(res, "XBooru")
            for post in data:
                if isinstance(post, dict) and 'directory' in post and 'image' in post:
                    post['file_url'] = f"https://xbooru.com/images/{post['directory']}/{post['image']}"
            result_count = len(data)
            if result_count == 0 and attempt == 0:
                max_pages = 2
                logger.debug("Processing %s results", result_count)
                continue
            logger.debug("Processing %s results", result_count)
            break
        return {'post': data}

    def get_data_page(self, add_tags, page=0, id=''):
        if id:
            add_tags = ''
        url = f"{self.base_url}&pid={page}{id}{add_tags}"
        self.booru_url = url
        res = self.fetch_with_retry(url, timeout=10)
        data = _response_posts_or_raise(res, "XBooru page")
        for post in data:
            if isinstance(post, dict) and 'directory' in post and 'image' in post:
                post['file_url'] = f"https://xbooru.com/images/{post['directory']}/{post['image']}"
        return {'post': data}

class Rule34(Booru):

    def __init__(self, api_key=None, user_id=None):
        super().__init__('rule34', f'https://api.rule34.xxx/index.php?page=dapi&s=post&q=index&json=1&limit={POST_AMOUNT}')
        self.api_key = api_key
        self.user_id = user_id

    def get_data(self, add_tags, max_pages=10, id=''):
        max_pages = _normalize_max_pages(max_pages)
        for attempt in range(2):
            if id:
                add_tags = ''
            url = f"{self.base_url}&pid={random.randint(0, max_pages-1)}{id}{add_tags}"
            if self.api_key and self.user_id:
                url += f"&api_key={self.api_key}&user_id={self.user_id}"
            self.booru_url = url
            res = self.fetch_with_retry(url, timeout=10)
            data = _response_posts_or_raise(res, "Rule34")
            result_count = len(data)
            if result_count == 0:
                max_pages = 2
                # Rule34 does not have a way to know the amount of results available in the search, so we need to run the function again with a fixed amount of pages
                if attempt == 0:
                    logger.debug("Processing %s results", result_count)
                continue
            else:
                logger.debug("Found enough results")
            break
        return {'post': data}

    def get_data_page(self, add_tags, page=0, id=''):
        if id:
            add_tags = ''
        url = f"{self.base_url}&pid={page}{id}{add_tags}"
        if self.api_key and self.user_id:
            url += f"&api_key={self.api_key}&user_id={self.user_id}"
        self.booru_url = url
        res = self.fetch_with_retry(url, timeout=10)
        data = _response_posts_or_raise(res, "Rule34 page")
        return {'post': data}

class Safebooru(Booru):

    def __init__(self):
        super().__init__('safebooru', f'https://safebooru.donmai.us/posts.json?limit={POST_AMOUNT}')

    def get_data(self, add_tags, max_pages=10, id='', tag_categories=None):
        max_pages = _normalize_max_pages(max_pages)
        for attempt in range(2):
            if id:
                add_tags = ''
            random_page = random.randint(1, max(1, int(max_pages)))
            url = f"{self.base_url}&page={random_page}{add_tags}"
            self.booru_url = url
            res = self.fetch_with_retry(url, headers=self.headers, timeout=10)
            data = _response_posts_or_raise(res, "Safebooru")
            result_count = 0
            for post in data:
                if isinstance(post, dict):
                    post['tags'] = self._filter_tags_by_category(post, tag_categories)
                    if not post.get('file_url') and post.get('large_file_url'):
                        post['file_url'] = post.get('large_file_url')
                    result_count += 1
            if result_count == 0:
                max_pages = 2
                if attempt == 0:
                    logger.debug("Processing %s results", result_count)
                continue
            else:
                logger.debug("Found enough results")
            break
        return {'post': data}

    def get_data_page(self, add_tags, page=0, id='', tag_categories=None):
        if id:
            add_tags = ''
        safe_page = max(1, int(page) + 1)
        url = f"{self.base_url}&page={safe_page}{add_tags}"
        self.booru_url = url
        res = self.fetch_with_retry(url, headers=self.headers, timeout=10)
        data = _response_posts_or_raise(res, "Safebooru page")
        for post in data:
            if isinstance(post, dict):
                post['tags'] = self._filter_tags_by_category(post, tag_categories)
                if not post.get('file_url') and post.get('large_file_url'):
                    post['file_url'] = post.get('large_file_url')
        return {'post': data}

    def get_post(self, add_tags, max_pages=10, id=''):
        if not id:
            return self.get_data(add_tags, max_pages, '', tag_categories=None)
        self.booru_url = f"https://safebooru.donmai.us/posts/{id}.json"
        res = self.fetch_with_retry(self.booru_url, headers=self.headers, timeout=10)
        data = res.json()
        if isinstance(data, dict):
            data['tags'] = data.get('tag_string', '')
            if not data.get('file_url') and data.get('large_file_url'):
                data['file_url'] = data.get('large_file_url')
            return {'post': [data]}
        return {'post': []}


class Konachan(Booru):

    def __init__(self):
        super().__init__('konachan', f'https://konachan.com/post.json?limit={POST_AMOUNT}')

    def get_data(self, add_tags, max_pages=10, id=''):
        max_pages = _normalize_max_pages(max_pages)
        for attempt in range(2):
            if id:
                add_tags = ''
            url = f"{self.base_url}&page={random.randint(0, max_pages-1)}{id}{add_tags}"
            self.booru_url = url
            res = self.fetch_with_retry(url, timeout=10)
            data = _response_posts_or_raise(res, "Konachan")
            result_count = len(data)
            if result_count == 0:
                max_pages = 2
                # Konachan does not have a way to know the amount of results available in the search, so we need to run the function again with a fixed amount of pages
                if attempt == 0:
                    logger.debug("Processing %s results", result_count)
                continue
            else:
                logger.debug("Found enough results")
            break
        return {'post': data}

    def get_data_page(self, add_tags, page=0, id=''):
        if id:
            add_tags = ''
        url = f"{self.base_url}&page={page}{id}{add_tags}"
        self.booru_url = url
        res = self.fetch_with_retry(url, timeout=10)
        data = _response_posts_or_raise(res, "Konachan page")
        return {'post': data}

    def get_post(self, add_tags, max_pages=10, id=''):
        raise Exception("Konachan does not support post IDs")


class Yandere(Booru):

    def __init__(self):
        super().__init__('yande.re', 'https://yande.re/post.json?api_version=2')

    def get_data(self, add_tags, max_pages=10, id=''):
        max_pages = _normalize_max_pages(max_pages)
        for attempt in range(2):
            if id:
                add_tags = ''
            page = random.randint(0, max_pages-1)
            extras = '&filter=1&include_tags=1&include_votes=1&include_pools=1'
            url = f"{self.base_url}&limit={POST_AMOUNT}&page={page}{id}{add_tags}{extras}"
            self.booru_url = url
            res = self.fetch_with_retry(url, timeout=10)
            posts = _response_posts_or_raise(res, "Yandere", keys=("posts",))
            result_count = len(posts)
            if result_count == 0:
                max_pages = 2
                # Yandere does not have a way to know the amount of results available in the search, so we need to run the function again with a fixed amount of pages
                if attempt == 0:
                    logger.debug("Processing %s results", result_count)
                continue
            else:
                logger.debug("Found enough results")
            break
        return {'post': posts}

    def get_data_page(self, add_tags, page=0, id=''):
        if id:
            add_tags = ''
        extras = '&filter=1&include_tags=1&include_votes=1&include_pools=1'
        url = f"{self.base_url}&limit={POST_AMOUNT}&page={page}{id}{add_tags}{extras}"
        self.booru_url = url
        res = self.fetch_with_retry(url, timeout=10)
        posts = _response_posts_or_raise(res, "Yandere page")
        return {'post': posts}

    def get_post(self, add_tags, max_pages=10, id=''):
        raise Exception("Yande.re does not support post IDs")


class AIBooru(Booru):

    def __init__(self):
        super().__init__('AIBooru', f'https://aibooru.online/posts.json?limit={POST_AMOUNT}')

    def get_data(self, add_tags, max_pages=10, id='', tag_categories=None):
        max_pages = _normalize_max_pages(max_pages)
        for attempt in range(2):
            if id:
                add_tags = ''
            url = f"{self.base_url}&page={random.randint(0, max_pages-1)}{id}{add_tags}"
            self.booru_url = url
            res = self.fetch_with_retry(url)
            data = _response_posts_or_raise(res, "AIBooru")
            for post in data:
                if isinstance(post, dict):
                    post['tags'] = self._filter_tags_by_category(post, tag_categories)
            result_count = len(data)
            if result_count == 0:
                max_pages = 2
                if attempt == 0:
                    logger.debug("Processing %s results", result_count)
                continue
            else:
                logger.debug("Found enough results")
            break
        return {'post': data}

    def get_data_page(self, add_tags, page=0, id='', tag_categories=None):
        if id:
            add_tags = ''
        url = f"{self.base_url}&page={page}{id}{add_tags}"
        self.booru_url = url
        res = self.fetch_with_retry(url)
        data = _response_posts_or_raise(res, "AIBooru page")
        for post in data:
            if isinstance(post, dict):
                post['tags'] = self._filter_tags_by_category(post, tag_categories)
        return {'post': data}

    def get_post(self, add_tags, max_pages=10, id=''):
        raise Exception("AIBooru does not support post IDs")


class Danbooru(Booru):

    def __init__(self):
        super().__init__('danbooru', f'https://danbooru.donmai.us/posts.json?limit={POST_AMOUNT}')

    def get_data(self, add_tags, max_pages=10, id='', tag_categories=None):
        max_pages = _normalize_max_pages(max_pages)
        for attempt in range(2):
            if id:
                add_tags = ''
            random_page = random.randint(1, max(1, int(max_pages)))
            url = f"{self.base_url}&page={random_page}{add_tags}"
            self.booru_url = url
            res = self.fetch_with_retry(url, headers=self.headers, timeout=10)
            data = _response_posts_or_raise(res, "Danbooru")
            for post in data:
                if isinstance(post, dict):
                    post['tags'] = self._filter_tags_by_category(post, tag_categories)
            result_count = len(data)
            if result_count == 0:
                max_pages = 2
                if attempt == 0:
                    logger.debug("Processing %s results", result_count)
                continue
            else:
                logger.debug("Found enough results")
            break
        return {'post': data}

    def get_data_page(self, add_tags, page=0, id='', tag_categories=None):
        if id:
            add_tags = ''
        safe_page = max(1, int(page) + 1)
        url = f"{self.base_url}&page={safe_page}{add_tags}"
        self.booru_url = url
        res = self.fetch_with_retry(url, headers=self.headers, timeout=10)
        data = _response_posts_or_raise(res, "Danbooru page")
        for post in data:
            if isinstance(post, dict):
                post['tags'] = self._filter_tags_by_category(post, tag_categories)
        return {'post': data}

    def get_post(self, add_tags, max_pages=10, id=''):
        if not id:
            return self.get_data(add_tags, max_pages, '', tag_categories=None)
        self.booru_url = f"https://danbooru.donmai.us/posts/{id}.json"
        res = self.fetch_with_retry(self.booru_url, headers=self.headers, timeout=10)
        data = res.json()
        if isinstance(data, dict):
            data['tags'] = data.get('tag_string', '')
            return {'post': [data]}
        return {'post': []}


def generate_chaos(pos_tags, neg_tags, chaos_amount):
    """Generates chaos in the prompt by adding random tags from the prompt to the positive and negative prompts

    Args:
        pos_tags (str): the positive prompt
        neg_tags (str): the negative prompt
        chaos_amount (float): the percentage of tags to put in the positive prompt

    Returns:
        str: the positive prompt
        str: the negative prompt
    """
    # create a list with the tags in the prompt and in the negative prompt
    chaos_list = [tag for tag in pos_tags.split(',') + neg_tags.split(',') if tag.strip() != '']
    # distinct the list
    chaos_list = list(set(chaos_list))
    random.shuffle(chaos_list)
    # put 50% of the tags in the prompt and the remaining 50% in the negative prompt
    len_list = round(len(chaos_list) * chaos_amount)
    pos_list = chaos_list[len_list:]
    pos_prompt = ','.join(pos_list)
    neg_list = chaos_list[:len_list]
    random.shuffle(neg_list)
    neg_prompt = ','.join(neg_list)
    return pos_prompt, neg_prompt


def resize_image(img, width, height, cropping=True):
    """Resize image to specified width and height

    Args:
        img (PIL.Image): the image
        width (int): the width in pixels
        height (int): the height in pixels
        cropping (bool): whether to crop the image or not

    Returns:
        PIL.Image: the resized image
    """
    if cropping:
        # resize the picture and center crop it
        # example: you have a 100x200 picture and width=300 and height=300
        # resize to 300x600 and crop to 300x300 from the center
        x, y = img.size
        if x < y:
            # scale to width keeping aspect ratio
            wpercent = (width / float(img.size[0]))
            hsize = int((float(img.size[1]) * float(wpercent)))
            img_new = img.resize((width, hsize))
            if img_new.size[1] < height:
                # scale to height keeping aspect ratio
                hpercent = (height / float(img.size[1]))
                wsize = int((float(img.size[0]) * float(hpercent)))
                img_new = img.resize((wsize, height))
        else:
            ypercent = (height / float(img.size[1]))
            wsize = int((float(img.size[0]) * float(ypercent)))
            img_new = img.resize((wsize, height))
            if img_new.size[0] < width:
                xpercent = (width / float(img.size[0]))
                hsize = int((float(img.size[1]) * float(xpercent)))
                img_new = img.resize((width, hsize))

        # crop center
        x, y = img_new.size
        left = (x - width) / 2
        top = (y - height) / 2
        right = (x + width) / 2
        bottom = (y + height) / 2
        img = img_new.crop((left, top, right, bottom))
    else:
        img = img.resize((width, height))
    return img

def modify_prompt(prompt, tagged_prompt, type_deepbooru):
    """Modifies the prompt based on the type_deepbooru selected

    Args:
        prompt (str): the prompt
        tagged_prompt (str): the prompt tagged by deepbooru
        type_deepbooru (str): the type of modification

    Returns:
        str: the modified prompt
    """
    if type_deepbooru == 'Add Before':
        return tagged_prompt + ',' + prompt
    elif type_deepbooru == 'Add After':
        return prompt + ',' + tagged_prompt
    elif type_deepbooru == 'Replace':
        return tagged_prompt
    return prompt

def remove_repeated_tags(prompt):
    """Removes the repeated tags keeping the same order

    Args:
        prompt (str): the prompt

    Returns:
        str: the prompt without repeated tags
    """
    prompt = prompt.split(',')
    new_prompt = []
    for tag in prompt:
        if tag not in new_prompt:
            new_prompt.append(tag)
    return ','.join(new_prompt)

def limit_prompt_tags(prompt, limit_tags, mode):
    """Limits the amount of tags in the prompt. It can be done by percentage or by a fixed amount.

    Args:
        prompt (str): the prompt
        limit_tags (float): the percentage of tags to keep
        mode (str): 'Limit' or 'Max'

    Returns:
        str: the prompt with the limited amount of tags
    """
    clean_prompt = prompt.split(',')
    if mode == 'Limit':
        clean_prompt = clean_prompt[:int(len(clean_prompt) * limit_tags)]
    elif mode == 'Max':
        clean_prompt = clean_prompt[:int(limit_tags)]
    return ','.join(clean_prompt)


# ─── 批量爬取辅助函数 ─────────────────────────────────────────────────────────
def _repeat_to_length(values, length):
    if isinstance(values, list):
        if not values:
            return ['' for _ in range(length)]
        return [str(values[i % len(values)] or '') for i in range(length)]
    return [str(values or '') for _ in range(length)]


def _append_tags(base_prompt, tags):
    base_prompt = str(base_prompt or '').strip()
    tags = str(tags or '').strip()
    if base_prompt and tags:
        return f'{base_prompt},{tags}'
    return base_prompt or tags


NO_POSTS_MESSAGE = 'Ranbooru: no posts found for these filters.'


def _format_ranbooru_error(booru, error):
    error_text = redact_sensitive(error)
    return (
        f'Ranbooru: [{booru}] request failed: {error_text}. '
        'Check proxy/TUN, lower Max Pages, or try another booru.'
    )


def _normalize_max_pages(value):
    try:
        return max(1, int(value))
    except (TypeError, ValueError, OverflowError):
        return 1


def _get_rating_tag(booru, mature_rating):
    if mature_rating == 'All':
        return ''
    rating = RATINGS.get(booru, {}).get(mature_rating)
    if not rating or rating == 'All':
        return ''
    return f'+rating:{rating}'


def _safe_read_text_file(base_dir, filename):
    if not filename:
        return ''
    safe_name = os.path.basename(str(filename))
    path = os.path.join(base_dir, safe_name)
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as file:
            return file.read()
    except Exception as error:
        logger.warning("Could not read %s: %s", path, error)
        return ''


def _safe_read_csv_file(base_dir, filename):
    return _safe_read_text_file(base_dir, filename).split(',')


def _normalize_tag_token(tag):
    normalized = re.sub(r'[\s_]+', '_', str(tag or '').strip().lower())
    normalized = re.sub(r'_+', '_', normalized).strip('_')
    if re.fullmatch(r'[+\-_]+', normalized or ''):
        return ''
    girl_match = re.fullmatch(r'(\d+)girls?', normalized)
    if girl_match:
        count = girl_match.group(1)
        return '1girl' if count == '1' else f'{count}girls'
    boy_match = re.fullmatch(r'(\d+)boys?', normalized)
    if boy_match:
        count = boy_match.group(1)
        return '1boy' if count == '1' else f'{count}boys'
    return normalized


def _tag_value_to_text(value):
    if not value:
        return ''
    if isinstance(value, dict):
        parts = []
        for category in ('general', 'artist', 'copyright', 'character', 'species', 'meta'):
            item = value.get(category, [])
            if isinstance(item, (list, tuple, set)):
                parts.extend(str(tag) for tag in item if str(tag or '').strip())
            elif item:
                parts.append(str(item))
        return ' '.join(parts)
    if isinstance(value, (list, tuple, set)):
        return ' '.join(str(tag) for tag in value if str(tag or '').strip())
    return str(value)


def _split_tag_tokens(value):
    if not value:
        return []
    normalized = _tag_value_to_text(value).replace('，', ',').replace('\n', ' ').replace('\r', ' ')
    tokens = []
    for item in re.split(r'[\s,]+', normalized):
        token = _normalize_tag_token(item)
        if token:
            tokens.append(token)
    return list(dict.fromkeys(tokens))


def _split_tag_filter(value):
    return _split_tag_tokens(value)


def _positive_search_tags(value):
    tokens = []
    for token in _split_tag_tokens(value):
        if not token or token.startswith('-') or token.startswith('rating:'):
            continue
        tokens.append(token)
    return list(dict.fromkeys(tokens))


def _join_tag_filters(*values):
    tokens = []
    for value in values:
        tokens.extend(_split_tag_filter(value))
    return ','.join(dict.fromkeys(tokens))


def _post_has_required_tags(tag_list, required_all='', required_any=''):
    normalized_tags = set(_split_tag_tokens(tag_list))
    required_all_tokens = _split_tag_filter(required_all)
    required_any_tokens = _split_tag_filter(required_any)
    if required_all_tokens and not all(tag in normalized_tags for tag in required_all_tokens):
        return False
    if required_any_tokens and not any(tag in normalized_tags for tag in required_any_tokens):
        return False
    return True


def _post_has_excluded_tags(tag_list, excluded_any=''):
    excluded_tokens = _split_tag_filter(excluded_any)
    if not excluded_tokens:
        return False
    normalized_tags = set(_split_tag_tokens(tag_list))
    return any(tag in normalized_tags for tag in excluded_tokens)


def _get_post_tags(post):
    if not isinstance(post, dict):
        return ''
    tags = post.get('tags', '')
    if not tags:
        tag_fields = [
            'tag_string',
            'tag_string_general',
            'tag_string_artist',
            'tag_string_copyright',
            'tag_string_character',
            'tag_string_species',
            'tag_string_meta',
        ]
        tags = ' '.join(str(post.get(field) or '') for field in tag_fields)
    return ' '.join(_split_tag_tokens(tags)).strip()


def _get_post_file_url(post):
    if not isinstance(post, dict):
        return ''
    for key in ('file_url', 'large_file_url', 'preview_file_url'):
        if post.get(key):
            return str(post.get(key))
    for nested_key in ('file', 'sample', 'preview'):
        nested = post.get(nested_key)
        if isinstance(nested, dict) and nested.get('url'):
            return str(nested.get('url'))
    return ''


def _normalize_posts(posts):
    normalized = []
    if not isinstance(posts, list):
        return normalized
    for post in posts:
        if not isinstance(post, dict):
            continue
        tags = _get_post_tags(post)
        if not tags:
            continue
        post['tags'] = tags
        file_url = _get_post_file_url(post)
        if file_url:
            post['file_url'] = file_url
        normalized.append(post)
    return normalized


def _fetch_booru_data(api_url, booru, add_tags, max_pages, post_id='', tag_categories=None):
    if post_id:
        return api_url.get_post(add_tags, max_pages, post_id)
    if booru in ['danbooru', 'safebooru', 'aibooru', 'e621'] and tag_categories:
        return api_url.get_data(add_tags, max_pages, '', tag_categories)
    return api_url.get_data(add_tags, max_pages)


def _fetch_booru_posts_with_fallback(
    api_url,
    booru,
    add_tags,
    max_pages,
    post_id='',
    tag_categories=None,
    plain_tags='',
    mature_rating='All',
    allow_animated_fallback=False,
    fallback_evidence=None,
):
    evidence = fallback_evidence if isinstance(fallback_evidence, dict) else {}
    evidence.clear()
    data = _fetch_booru_data(
        api_url,
        booru,
        add_tags,
        max_pages,
        post_id,
        tag_categories,
    )
    posts = _normalize_posts(data.get('post', []) if isinstance(data, dict) else [])
    evidence.update({
        "status": "ok" if posts else "empty",
        "initial_post_count": len(posts),
        "fallback_post_count": 0,
        "fallback_authorized": bool(allow_animated_fallback),
    })
    can_relax_animated = (
        not posts
        and booru == 'rule34'
        and add_tags.startswith('&tags=-animated')
    )
    if can_relax_animated and not allow_animated_fallback:
        evidence["status"] = "empty_without_fallback"
    elif can_relax_animated:
        fallback_add_tags = '&tags='
        if plain_tags:
            fallback_add_tags += str(plain_tags).replace(',', '+')
        fallback_add_tags += _get_rating_tag(booru, mature_rating)
        evidence.update({
            "status": "fallback_requested",
            "fallback_query": fallback_add_tags,
        })
        try:
            data = api_url.get_data(fallback_add_tags, max_pages)
        except Exception:
            evidence["status"] = "fallback_failed"
            raise
        posts = _normalize_posts(data.get('post', []) if isinstance(data, dict) else [])
        evidence.update({
            "status": "fallback_succeeded" if posts else "fallback_empty",
            "fallback_post_count": len(posts),
        })
    return posts


class _BooruPipelineClient:
    """Adapts the Forge entry module's HTTP clients to the pure pipeline protocol."""

    def __init__(self, client, request, add_tags, allow_animated_fallback=False):
        self.client = client
        self.request = request
        self.add_tags = add_tags
        self.allow_animated_fallback = bool(allow_animated_fallback)
        self.fetch_evidence = {}

    def fetch(self):
        try:
            return _fetch_booru_posts_with_fallback(
                self.client,
                self.request.service,
                self.add_tags,
                self.request.max_pages,
                self.request.post_id,
                self.request.tag_categories,
                self.request.tags,
                self.request.mature_rating,
                self.allow_animated_fallback,
                self.fetch_evidence,
            )
        except requests.exceptions.RequestException as error:
            raise RetryExhaustedError(str(error)) from error

    def close(self):
        self.client.close()


def _pipeline_request_tags(service, tags, mature_rating):
    add_tags = '&tags=-animated'
    if tags:
        add_tags += '+' + str(tags).replace(',', '+')
    return add_tags + _get_rating_tag(service, mature_rating)


def _adapt_pipeline_client(client, request, allow_animated_fallback):
    return _BooruPipelineClient(
        client,
        request,
        _pipeline_request_tags(request.service, request.tags, request.mature_rating),
        allow_animated_fallback=(request.service == 'rule34' and allow_animated_fallback),
    )


def _pipeline_saved_credentials(service):
    if service not in ('gelbooru', 'rule34'):
        return {}
    saved = credentials_manager.get_booru_credentials(service)
    return {
        service: CredentialInput(
            service=service,
            api_key=str(saved.get('api_key') or ''),
            user_id=str(saved.get('user_id') or ''),
        )
    }


def _pipeline_client_factory(use_cache, fringe_benefits):
    def factory(request, credential):
        client = _create_booru_api(
            request.service,
            fringe_benefits,
            credential.api_key if request.service == 'gelbooru' else None,
            credential.user_id if request.service == 'gelbooru' else None,
            credential.api_key if request.service == 'rule34' else None,
            credential.user_id if request.service == 'rule34' else None,
        )
        client.configure_http_cache(use_cache)
        return _adapt_pipeline_client(client, request, fringe_benefits)

    return factory


def _create_booru_api(
    booru,
    fringe_benefits=True,
    gelbooru_api_key=None,
    gelbooru_user_id=None,
    rule34_api_key=None,
    rule34_user_id=None,
):
    factories = {
        'gelbooru': lambda: Gelbooru(fringe_benefits, gelbooru_api_key, gelbooru_user_id),
        'rule34': lambda: Rule34(rule34_api_key, rule34_user_id),
        'safebooru': Safebooru,
        'danbooru': Danbooru,
        'konachan': Konachan,
        'yande.re': Yandere,
        'aibooru': AIBooru,
        'xbooru': XBooru,
        'e621': e621,
    }
    return factories.get(booru, factories['gelbooru'])()


def _fetch_image(fetcher, url, headers=None):
    if not url:
        return None
    try:
        response = fetcher(url, headers=headers or {}, timeout=10)
        image = Image.open(BytesIO(response.content))
        image.load()
        return image.convert('RGB')
    except Exception as error:
        log_event(logger, "image_failure", "Could not fetch image %s: %s", redact_sensitive(url), error)
        return None


def _send_to_controlnet_legacy(p, image, denoising):
    try:
        controlnet_module = importlib.import_module(
            'extensions.sd-webui-controlnet.scripts.external_code',
            'external_code',
        )
        get_units = getattr(controlnet_module, 'get_all_units_in_processing', None)
        update_units = getattr(controlnet_module, 'update_cn_script_in_processing', None)
        if get_units is None or update_units is None:
            logger.warning("ControlNet legacy API is unavailable; skipping Send to Controlnet")
            return False
        controlnet_units = list(get_units(p) or [])
        if not controlnet_units:
            logger.warning("No ControlNet units found; skipping Send to Controlnet")
            return False
        copied_network = controlnet_units[0].__dict__.copy()
        copied_network['enabled'] = True
        copied_network['weight'] = denoising
        copied_network.setdefault('image', {})
        if not isinstance(copied_network['image'], dict):
            copied_network['image'] = {}
        copied_network['image']['image'] = np.array(image)
        update_units(p, [copied_network] + controlnet_units[1:])
        return True
    except Exception as error:
        logger.error("ControlNet send failed: %s", error)
        return False


def _sync_prompt_lists(p):
    prompts = p.prompt if isinstance(p.prompt, list) else [p.prompt]
    negative_prompts = (
        p.negative_prompt if isinstance(p.negative_prompt, list) else [p.negative_prompt]
    )
    prompt_count = max(len(prompts), len(negative_prompts), 1)
    p.all_prompts = _repeat_to_length(prompts, prompt_count)
    p.all_negative_prompts = _repeat_to_length(negative_prompts, prompt_count)
    if prompt_count > 1:
        p.prompt = p.all_prompts
        p.negative_prompt = p.all_negative_prompts
    else:
        p.prompt = p.all_prompts[0]
        p.negative_prompt = p.all_negative_prompts[0]
    p.n_iter = max(1, (prompt_count + max(1, p.batch_size) - 1) // max(1, p.batch_size))


def batch_fetch_tags(booru_name, tags_search, max_pages, fringe_benefits,
                     remove_bad_tags, remove_tags_str, shuffle_tags, change_dash,
                     limit_tags, max_tags, mature_rating,
                     api_key='', user_id_str='', save_credentials=False,
                     use_remove_txt=False, choose_remove_txt='', tag_categories=None,
                     min_score_enabled=False, min_score=0,
                     cache_start_page=1,
                     cache_keep_all_tags='', cache_keep_any_tags='',
                     cache_exclude_tags=''):
    """Fetch posts from booru and return metadata records with cleaned prompt tags."""
    max_pages = _normalize_max_pages(max_pages)
    try:
        start_page_index = max(0, int(cache_start_page or 1) - 1)
    except Exception:
        start_page_index = 0

    gelbooru_api_key = None
    gelbooru_user_id = None
    rule34_api_key = None
    rule34_user_id = None
    if booru_name == 'gelbooru':
        if api_key.strip() and user_id_str.strip():
            gelbooru_api_key = api_key.strip()
            gelbooru_user_id = user_id_str.strip()
            if save_credentials:
                credentials_manager.save_booru_credentials('gelbooru', gelbooru_api_key, gelbooru_user_id)
        else:
            saved = credentials_manager.get_booru_credentials('gelbooru')
            gelbooru_api_key = saved.get('api_key', '')
            gelbooru_user_id = saved.get('user_id', '')
    if booru_name == 'rule34':
        if api_key.strip() and user_id_str.strip():
            rule34_api_key = api_key.strip()
            rule34_user_id = user_id_str.strip()
            if save_credentials:
                credentials_manager.save_booru_credentials('rule34', rule34_api_key, rule34_user_id)
        else:
            saved = credentials_manager.get_booru_credentials('rule34')
            rule34_api_key = saved.get('api_key', '')
            rule34_user_id = saved.get('user_id', '')

    api = _create_booru_api(
        booru_name,
        fringe_benefits,
        gelbooru_api_key,
        gelbooru_user_id,
        rule34_api_key,
        rule34_user_id,
    )

    add_tags = '&tags=-animated'
    if tags_search:
        add_tags += '+' + tags_search.replace(',', '+')
    add_tags += _get_rating_tag(booru_name, mature_rating)
    effective_keep_all_tags = _join_tag_filters(_positive_search_tags(tags_search), cache_keep_all_tags)

    # Build bad_tags
    bad_tags = []
    if remove_bad_tags:
        bad_tags = list(DEFAULT_BAD_TAGS)
    if remove_tags_str:
        if ',' in remove_tags_str:
            bad_tags.extend(remove_tags_str.split(','))
        else:
            bad_tags.append(remove_tags_str)
    if use_remove_txt and choose_remove_txt:
        bad_tags.extend(_safe_read_csv_file(user_remove_dir, choose_remove_txt))
    bad_tag_exact = {
        _normalize_tag_token(tag)
        for tag in bad_tags
        if str(tag or '').strip() and '*' not in str(tag)
    }
    bad_tag_wildcards = [
        _normalize_tag_token(str(tag).replace('*', ''))
        for tag in bad_tags
        if str(tag or '').strip() and '*' in str(tag)
    ]

    records = []
    stats = {
        "complete": True,
        "error": "",
        "error_page": None,
        "stop_reason": "requested_pages_completed",
        "pages_done": 0,
        "posts_seen": 0,
        "kept": 0,
        "start_page": start_page_index + 1,
        "end_page": start_page_index + max_pages,
        "skipped_score": 0,
        "skipped_filter": 0,
        "skipped_exclude": 0,
        "skipped_empty": 0,
    }
    min_score_value = int(min_score or 0)
    for page in range(start_page_index, start_page_index + max_pages):
        try:
            if hasattr(api, 'get_data_page'):
                if booru_name in ['danbooru', 'safebooru', 'aibooru', 'e621'] and tag_categories:
                    data = api.get_data_page(add_tags, page, '', tag_categories)
                else:
                    data = api.get_data_page(add_tags, page)
            else:
                data = api.get_data(add_tags, max_pages=1)
            raw_posts = data.get('post', [])
            if not isinstance(raw_posts, list):
                raw_posts = []
            if len(raw_posts) == 0:
                logger.info("Tag cache page %s returned no data; stopping", page + 1)
                stats["stop_reason"] = "empty_page"
                break
            posts = _normalize_posts(raw_posts)
            stats["pages_done"] += 1
            stats["posts_seen"] += len(raw_posts)
            for post in posts:
                if not isinstance(post, dict):
                    continue
                try:
                    score = int(float(post.get('score') or 0))
                except Exception:
                    score = 0
                if min_score_enabled and score < min_score_value:
                    stats["skipped_score"] += 1
                    continue
                raw_tags = _get_post_tags(post)
                if not raw_tags:
                    stats["skipped_empty"] += 1
                    continue
                tag_list = _split_tag_tokens(raw_tags)
                if not _post_has_required_tags(tag_list, effective_keep_all_tags, cache_keep_any_tags):
                    stats["skipped_filter"] += 1
                    continue
                if _post_has_excluded_tags(tag_list, cache_exclude_tags):
                    stats["skipped_exclude"] += 1
                    continue
                if shuffle_tags:
                    random.shuffle(tag_list)
                tag_list = [t for t in tag_list if t not in bad_tag_exact]
                for bt in bad_tag_wildcards:
                    if bt:
                        tag_list = [t for t in tag_list if bt not in t]
                # Join with commas
                prompt_str = ','.join(tag_list)
                if change_dash:
                    prompt_str = prompt_str.replace('_', ' ')
                if limit_tags < 1:
                    prompt_str = limit_prompt_tags(prompt_str, limit_tags, 'Limit')
                if max_tags > 0:
                    prompt_str = limit_prompt_tags(prompt_str, int(max_tags), 'Max')
                if prompt_str.strip():
                    post_id = post.get('id') or post.get('post_id') or post.get('md5') or ''
                    records.append({
                        "booru": booru_name,
                        "post_id": post_id,
                        "tags_raw": raw_tags,
                        "tags_prompt": prompt_str,
                        "score": score,
                        "rating": post.get('rating') or '',
                        "source_url": post.get('source') or post.get('file_url') or post.get('sample_url') or post.get('jpeg_url') or '',
                        "preview_url": post.get('preview_url') or post.get('preview_file_url') or post.get('sample_url') or '',
                        "search_query": tags_search or '',
                    })
                    stats["kept"] += 1
                else:
                    stats["skipped_empty"] += 1
            logger.info(
                "Tag cache page %s/%s complete: fetched %s, normalized %s",
                page + 1,
                start_page_index + max_pages,
                len(raw_posts),
                len(posts),
            )
        except Exception as ex:
            logger.error("Tag cache page %s failed: %s", page + 1, ex)
            stats["complete"] = False
            stats["error"] = str(ex)
            stats["error_page"] = page + 1
            stats["stop_reason"] = "error"
            break

    api.close()
    return records, stats


def _is_hires_second_pass(p):
    return bool(getattr(p, "is_hr_pass", False))


def _processing_job_id(p):
    state_job_id = getattr(shared.state, "job_timestamp", None)
    if state_job_id:
        return str(state_job_id)
    return (
        f"{getattr(p, 'seed', '')}:{getattr(p, 'n_iter', '')}:"
        f"{getattr(p, 'batch_size', '')}:{id(p)}"
    )


class Script(scripts.Script):
    def __init__(self):
        super().__init__()
        self.prompt_area = [None, None]
        self.prompt_row = [None, None]
        self.input_row = [None, None]
        self.action_row = [None, None]
        self.on_after_component_elem_id = [
            ("txt2img_prompt_row", lambda x: self.create_prompt_row(0, x)),
            ("img2img_prompt_row", lambda x: self.create_prompt_row(1, x)),
            ("txt2img_prompt", lambda x: self.set_prompt_area(0, x)),
            ("img2img_prompt", lambda x: self.set_prompt_area(1, x)),
        ]
        self._local_cache_applied_jobs = {}   # Insertion-ordered bounded job dedupe map.
        self._active_job_id = None
        self.last_img = []
        self.real_steps = 0
        self.previous_loras = ''
        self.original_prompt = ''

    def create_prompt_row(self, i2i, component):
        self.prompt_row[i2i] = gr.Row()
        self.input_row[i2i] = gr.Row()
        self.action_row[i2i] = gr.Row()

    def set_prompt_area(self, i2i, component):
        try:
            self.prompt_area[i2i] = component.component if hasattr(component, "component") else component
        except Exception:
            self.prompt_area[i2i] = None
    version = __version__

    def get_files(self, path):
        files = []
        for file in os.listdir(path):
            if file.endswith('.txt'):
                files.append(file)
        return files

    def title(self):
        return "Ranbooru"

    def show(self, is_img2img):
        return scripts.AlwaysVisible

    def show_gelbooru_api_fields(self, booru):
        """Show/hide API key fields based on selected booru"""
        if booru in ['gelbooru', 'rule34']:
            return gr.update(visible=True)
        else:
            return gr.update(visible=False)

    def load_gelbooru_credentials(self, booru):
        """Load saved credentials for selected booru"""
        if booru in ['gelbooru', 'rule34']:
            has_saved_creds = credentials_manager.has_credentials(booru)

            if has_saved_creds:
                # Hide input fields and show file path
                status_text = "✓ Credentials are configured on the server"
                return (
                    gr.update(visible=False, value=""),  # api_key field
                    gr.update(visible=False, value=""),  # user_id field
                    gr.update(visible=True, value=status_text),  # credentials_status
                    gr.update(visible=True, value="Clear saved credentials")  # clear_credentials_btn
                )
            else:
                # Show input fields
                return (
                    gr.update(visible=True, value=""),  # api_key field
                    gr.update(visible=True, value=""),  # user_id field
                    gr.update(visible=False, value=""),  # credentials_status
                    gr.update(visible=False)  # clear_credentials_btn
                )
        else:
            return (
                gr.update(visible=False, value=""),  # api_key field
                gr.update(visible=False, value=""),  # user_id field
                gr.update(visible=False, value=""),  # credentials_status
                gr.update(visible=False)  # clear_credentials_btn
            )

    def clear_gelbooru_credentials(self, booru="gelbooru"):
        credentials_manager.clear_booru_credentials(booru)
        return (
            gr.update(visible=True, value=""),
            gr.update(visible=True, value=""),
            gr.update(visible=False, value=""),
            gr.update(visible=False)
        )

    def refresh_ser(self):
        return gr.update(choices=self.get_files(user_search_dir))
    def refresh_rem(self):
        return gr.update(choices=self.get_files(user_remove_dir))

    # ─── Tag Cache 面板回调 ───────────────────────────────────────────────
    @staticmethod
    def _cache_batch_fetch(booru, tags_search, max_pages, cache_start_page, fringe_benefits,
                           remove_bad_tags, remove_tags_str, shuffle_tags,
                           change_dash, limit_tags, max_tags, mature_rating,
                           api_key, user_id_str, save_credentials,
                           use_remove_txt, choose_remove_txt, append_mode, tag_categories,
                           cache_dedupe, min_score_enabled, min_score,
                           cache_keep_all_tags, cache_keep_any_tags, cache_exclude_tags):
        """Batch fetch posts, clean tags, and save them into the local cache."""
        try:
            records, fetch_stats = batch_fetch_tags(
                booru, tags_search, max_pages, fringe_benefits,
                remove_bad_tags, remove_tags_str, shuffle_tags, change_dash,
                limit_tags, max_tags, mature_rating,
                api_key, user_id_str, save_credentials,
                use_remove_txt, choose_remove_txt, tag_categories,
                min_score_enabled, min_score,
                cache_start_page,
                cache_keep_all_tags, cache_keep_any_tags, cache_exclude_tags,
            )
            if not fetch_stats.get("complete", True) and not append_mode:
                error_page = fetch_stats.get("error_page") or "?"
                error = fetch_stats.get("error") or "未知错误"
                return (
                    f"覆盖保存已取消：抓取在第 {error_page} 页中断（{error}）。"
                    "原缓存保持不变；如需保留已抓取部分，请改用追加模式。",
                    tag_cache_manager.get_status(),
                )
            if not records:
                error = fetch_stats.get("error")
                suffix = f"；抓取错误：{error}" if error else ""
                return f"未抓取到符合缓存过滤条件的 tag 数据{suffix}", tag_cache_manager.get_status()
            if append_mode:
                save_stats = tag_cache_manager.append_records(records, dedupe=cache_dedupe)
                action = "追加完成"
                if not fetch_stats.get("complete", True):
                    action = "部分抓取已追加"
            else:
                save_stats = tag_cache_manager.save_records(records, dedupe=cache_dedupe)
                tag_cache_manager.reset_index()
                action = "覆盖保存完成"
            backup_note = f"，覆盖前备份 {save_stats.get('backup_path')}" if save_stats.get("backup_path") else ""
            msg = (
                f"{action}: 写入 {save_stats['inserted']} 条，"
                f"重复跳过 {save_stats['skipped_duplicate']} 条，"
                f"低分跳过 {fetch_stats['skipped_score']} 条，"
                f"缓存过滤跳过 {fetch_stats['skipped_filter']} 条，"
                f"排除 tag 跳过 {fetch_stats.get('skipped_exclude', 0)} 条，"
                f"空 tag 跳过 {fetch_stats['skipped_empty']} 条，"
                f"页段 {fetch_stats['start_page']}-{fetch_stats['start_page'] + max(fetch_stats['pages_done'] - 1, 0)}，"
                f"缓存总计 {save_stats['total']} 条{backup_note}"
            )
            if not fetch_stats.get("complete", True):
                msg += (
                    f"；警告：第 {fetch_stats.get('error_page', '?')} 页抓取失败："
                    f"{fetch_stats.get('error') or '未知错误'}"
                )
            return msg, tag_cache_manager.get_status()
        except Exception as ex:
            return f"错误: {ex}", tag_cache_manager.get_status()

    @staticmethod
    def _cache_get_next(loop_mode, prefer_natural=False):
        """从缓存顺序取出下一条"""
        tags, idx, total = tag_cache_manager.get_next_tags_from_pool(
            loop=loop_mode,
            prefer_natural=prefer_natural,
        )
        if tags is None:
            if total == 0:
                return "缓存为空，请先批量爬取", tag_cache_manager.get_status(), ""
            else:
                return (
                    f"已到达缓存末尾 (索引 {idx}/{total})",
                    tag_cache_manager.get_status(),
                    "",
                )
        return tags, tag_cache_manager.get_status(), str(tag_cache_manager.tag_id_at_position(idx) or "")

    @staticmethod
    def _cache_reset_index():
        tag_cache_manager.reset_index()
        return "✅ 索引已重置为 0", tag_cache_manager.get_status()

    @staticmethod
    def _cache_delete():
        result = tag_cache_manager.delete_cache()
        if result.get("error"):
            return f"删除缓存失败: {result['error']}", tag_cache_manager.get_status()
        backup_note = f"，备份: {result.get('backup_path')}" if result.get("backup_path") else ""
        return f"✅ 缓存文件已删除，可撤销 {result.get('deleted', 0)} 条{backup_note}", tag_cache_manager.get_status()

    @staticmethod
    def _cache_backup():
        try:
            path = tag_cache_manager.backup_db("manual")
            if not path:
                return "当前没有可备份的缓存数据库"
            return f"已备份缓存数据库：{path}"
        except Exception as e:
            return f"备份失败: {e}"

    @staticmethod
    def _cache_restore_last_deleted(dedupe=True):
        result = tag_cache_manager.restore_last_deleted(dedupe=dedupe)
        if not result.get("ok"):
            return result.get("message", "撤销失败"), tag_cache_manager.get_status()
        return (
            f"撤销完成: 上次删除 {result.get('deleted_count', 0)} 条，恢复写入 {result.get('inserted', 0)} 条，"
            f"重复跳过 {result.get('skipped_duplicate', 0)} 条，总计 {result.get('total', 0)} 条",
            tag_cache_manager.get_status(),
        )

    @staticmethod
    def _cache_compact_duplicates():
        stats = tag_cache_manager.compact_duplicates()
        if stats.get("error"):
            return f"重复整理失败: {stats['error']}", tag_cache_manager.get_status()
        backup_note = f"，备份: {stats.get('backup_path')}" if stats.get("backup_path") else ""
        return (
            f"重复整理完成: 重建 {stats['updated']} 条，"
            f"删除重复 {stats['removed']} 条，总计 {stats['total']} 条{backup_note}"
        ), tag_cache_manager.get_status()

    @staticmethod
    def _cache_compact_similar(threshold, keep_per_group):
        stats = tag_cache_manager.compact_similar(threshold, keep_per_group)
        if stats.get("error"):
            return f"相似整理失败: {stats['error']}", tag_cache_manager.get_status()
        backup_note = f"，备份: {stats.get('backup_path')}" if stats.get("backup_path") else ""
        return (
            f"相似整理完成: 阈值 {stats['threshold']:.2f}，每组保留 {stats['keep_per_group']} 条，"
            f"检查 {stats['checked']} 条，删除完全一致 {stats['exact_removed']} 条，"
            f"删除相似 {stats['similar_removed']} 条，总计 {stats['total']} 条{backup_note}"
        ), tag_cache_manager.get_status()

    @staticmethod
    def _cache_search(keyword):
        """搜索缓存"""
        results = tag_cache_manager.search_cache(keyword)
        return results if results else []

    @staticmethod
    def _parse_cache_id(tag_id):
        if tag_id is None:
            return None
        match = re.search(r"\d+", str(tag_id))
        return int(match.group(0)) if match else None

    @staticmethod
    def _combine_prompt(base_prompt, tags):
        if base_prompt and str(base_prompt).strip():
            return f"{str(base_prompt).strip()},{tags}"
        return tags

    @staticmethod
    def _apply_write_mode(current_prompt, tags, write_mode):
        current_prompt = str(current_prompt or "").strip()
        tags = str(tags or "").strip()
        if not tags or write_mode == "只输出":
            return current_prompt
        if write_mode == "替换":
            return tags
        if write_mode == "追加到前面":
            return f"{tags},{current_prompt}" if current_prompt else tags
        return f"{current_prompt},{tags}" if current_prompt else tags

    def _apply_local_cache_prompt_pipeline(
        self,
        p,
        cache_prompts,
        natural_prompt_flags,
        write_mode,
        same_prompt,
        shuffle_tags,
        remove_bad_tags,
        remove_tags,
        change_background,
        change_color,
        chaos_mode,
        negative_mode,
        chaos_amount,
        limit_tags,
        max_tags,
        use_same_seed,
    ):
        """Apply prompt-only options to a frozen local-cache batch."""
        total_images = max(1, int(p.batch_size) * int(p.n_iter))
        prompts = _repeat_to_length(cache_prompts, total_images)
        natural_prompt_flags = [
            bool(value)
            for value in _repeat_to_length(natural_prompt_flags, total_images)
        ]
        if same_prompt and prompts:
            prompts = [prompts[0] for _ in range(total_images)]
            natural_prompt_flags = [natural_prompt_flags[0] for _ in range(total_images)]

        bad_tags = list(DEFAULT_BAD_TAGS) if remove_bad_tags else []
        if str(remove_tags or "").strip():
            bad_tags.extend(_split_tag_tokens(remove_tags))
        background_options = {
            'Add Background': ('detailed_background,' + random.choice(["outdoors", "indoors"]), COLORED_BG),
            'Remove Background': ('plain_background,simple_background,' + random.choice(COLORED_BG), ADD_BG),
            'Remove All': ('', COLORED_BG + ADD_BG),
        }
        prompt_addition = ''
        if change_background in background_options:
            prompt_addition, background_bad_tags = background_options[change_background]
            bad_tags.extend(background_bad_tags)
        color_options = {
            'Colored': BW_BG,
            'Limited Palette': '(limited_palette:1.3)',
            'Monochrome': ','.join(BW_BG),
        }
        if change_color in color_options:
            color_value = color_options[change_color]
            if isinstance(color_value, list):
                bad_tags.extend(color_value)
            else:
                prompt_addition = _append_tags(prompt_addition, color_value)

        exact_bad = {_normalize_tag_token(tag) for tag in bad_tags if '*' not in str(tag)}
        wildcard_bad = [
            _normalize_tag_token(str(tag).replace('*', ''))
            for tag in bad_tags
            if '*' in str(tag)
        ]
        cleaned_prompts = []
        for cached_prompt, is_natural in zip(prompts, natural_prompt_flags):
            cached_prompt = str(cached_prompt or '').strip()
            if is_natural:
                cleaned = cached_prompt
                cleaned_prompts.append(cleaned)
                continue
            pieces = [piece.strip() for piece in str(cached_prompt or '').split(',') if piece.strip()]
            filtered = []
            for piece in pieces:
                normalized = _normalize_tag_token(piece)
                if normalized in exact_bad:
                    continue
                if any(token and token in normalized for token in wildcard_bad):
                    continue
                filtered.append(piece)
            if shuffle_tags:
                random.shuffle(filtered)
            cleaned = ','.join(filtered)
            cleaned_prompts.append(_append_tags(prompt_addition, cleaned))

        base_prompts = _repeat_to_length(p.prompt, total_images)
        negative_prompts = _repeat_to_length(p.negative_prompt, total_images)
        combined_prompts = [
            self._apply_write_mode(base_prompt, cached_prompt, write_mode)
            for base_prompt, cached_prompt in zip(base_prompts, cleaned_prompts)
        ]

        positive_results = []
        negative_results = []
        for combined_prompt, base_prompt, negative_prompt, cached_prompt, is_natural in zip(
            combined_prompts,
            base_prompts,
            negative_prompts,
            cleaned_prompts,
            natural_prompt_flags,
        ):
            if is_natural:
                positive_results.append(combined_prompt)
                negative_results.append(negative_prompt)
            elif negative_mode == 'Negative':
                positive_results.append(base_prompt)
                negative_results.append(_append_tags(negative_prompt, cached_prompt))
            elif chaos_mode in ['Chaos', 'Less Chaos']:
                chaos_negative = '' if chaos_mode == 'Less Chaos' else negative_prompt
                positive, generated_negative = generate_chaos(
                    combined_prompt,
                    chaos_negative,
                    chaos_amount,
                )
                positive_results.append(positive)
                negative_results.append(
                    _append_tags(negative_prompt, generated_negative)
                    if chaos_mode == 'Less Chaos'
                    else generated_negative
                )
            else:
                positive_results.append(combined_prompt)
                negative_results.append(negative_prompt)
        p.prompt = positive_results
        p.negative_prompt = negative_results

        if limit_tags < 1:
            p.prompt = [
                prompt if is_natural else limit_prompt_tags(prompt, limit_tags, 'Limit')
                for prompt, is_natural in zip(p.prompt, natural_prompt_flags)
            ]
        if max_tags > 0:
            p.prompt = [
                prompt if is_natural else limit_prompt_tags(prompt, max_tags, 'Max')
                for prompt, is_natural in zip(p.prompt, natural_prompt_flags)
            ]
        if use_same_seed:
            seed = random.randint(0, 2 ** 32 - 1) if p.seed == -1 else p.seed
            p.seed = [seed] * total_images
        _sync_prompt_lists(p)
        return p

    @staticmethod
    def _cache_fill_by_position(position, prefer_natural=False):
        """根据真实缓存序号填充标签"""
        cache_position = Script._parse_cache_id(position)
        if cache_position is None:
            return "", "请输入有效的缓存序号", ""
        tags = tag_cache_manager.get_by_position(
            cache_position,
            prefer_natural=prefer_natural,
        )
        if tags:
            prompt_kind = "自然语言 Prompt" if prefer_natural else "原始 Tag"
            return tags, f"已取出第 {cache_position} 条（优先使用{prompt_kind}）", str(tag_cache_manager.tag_id_at_position(cache_position) or "")
        return "", f"未找到第 {cache_position} 条", ""

    @staticmethod
    def _cache_fill_by_id(tag_id, prefer_natural=False):
        stable_id = Script._parse_cache_id(tag_id)
        if stable_id is None:
            return "", "请输入搜索结果中的内部 ID", ""
        tags = tag_cache_manager.get_by_id(stable_id, prefer_natural=prefer_natural)
        if tags:
            prompt_kind = "自然语言 Prompt" if prefer_natural else "原始 Tag"
            return tags, f"已按内部 ID {stable_id} 取出（优先使用{prompt_kind}）", str(stable_id)
        return "", f"未找到内部 ID {stable_id}", ""

    @staticmethod
    def _cache_fill_position_to_tag_prompt(
        position,
        tag_prompt_text,
        write_mode,
        prefer_natural=False,
    ):
        """Fill Tag Prompt with a cached tag row by visible cache position."""
        tags, msg, record_id = Script._cache_fill_by_position(position, prefer_natural)
        if not tags:
            return "", msg, tag_prompt_text, ""
        return (
            tags,
            msg,
            Script._apply_write_mode(tag_prompt_text, tags, write_mode),
            record_id,
        )

    @staticmethod
    def _cache_jump_to_position(position):
        """Move the sequential cache cursor so the next read starts at a 1-based position."""
        result = tag_cache_manager.jump_to_position(position)
        if result.get("ok"):
            return (
                f"已跳转到第 {result['position']} / {result['total']} 条；下一次顺序取出会从这里开始",
                Script._cache_refresh_status(),
            )
        return result.get("message", "跳转失败"), Script._cache_refresh_status()

    @staticmethod
    def _cache_delete_by_id(tag_id):
        stable_id = Script._parse_cache_id(tag_id)
        if stable_id is None:
            return "请输入搜索结果中的内部 ID", tag_cache_manager.get_status()
        deleted = tag_cache_manager.delete_by_ids([stable_id])
        if deleted:
            return f"已删除内部 ID {stable_id}", tag_cache_manager.get_status()
        return f"删除失败：未找到内部 ID {stable_id}", tag_cache_manager.get_status()

    @staticmethod
    def _cache_delete_by_tags(tags):
        if not str(tags or "").strip():
            return "请输入要删除的 tag，例如 comic,text,speech_bubble", tag_cache_manager.get_status()
        count = tag_cache_manager.delete_by_any_tags(tags)
        return f"已删除包含指定 tag 的 {count} 条缓存", tag_cache_manager.get_status()

    @staticmethod
    def _cache_refresh_status():
        return tag_cache_manager.get_status()

    @staticmethod
    def _format_cache_preview(record):
        if not record:
            return "缓存为空或没有下一条"
        tags = str(record.get("tags_prompt") or "")
        tag_preview = tags[:220] + ("..." if len(tags) > 220 else "")
        meta = []
        if record.get("booru"):
            meta.append(str(record["booru"]))
        if record.get("post_id"):
            meta.append(f"post {record['post_id']}")
        meta.append(f"score {record.get('score', 0)}")
        if record.get("rating"):
            meta.append(f"rating {record['rating']}")
        return f"第 {record['position']} 条 | {' | '.join(meta)}\n{tag_preview}"

    @staticmethod
    def _format_delete_preview(result):
        if not result.get("ok"):
            return result.get("message", "预览失败")
        lines = [
            f"{result.get('title', '删除预览')}: 将删除 {result.get('count', 0)} 条"
        ]
        requested = result.get("requested")
        if requested is not None and requested != result.get("count"):
            lines[0] += f"（请求 {requested} 个序号，忽略不存在/重复项）"
        if not result.get("sample"):
            lines.append("没有命中记录，当前缓存不会被改变。")
            return "\n".join(lines)
        lines.append("样本：")
        for record in result.get("sample", []):
            tags = str(record.get("tags_prompt") or "")
            tag_preview = tags[:140] + ("..." if len(tags) > 140 else "")
            meta = []
            if record.get("booru"):
                meta.append(str(record["booru"]))
            if record.get("post_id"):
                meta.append(f"post {record['post_id']}")
            if record.get("score") not in (None, ""):
                meta.append(f"score {record.get('score', 0)}")
            prefix = f"第 {record.get('position', '?')} 条"
            if meta:
                prefix += f" | {' | '.join(meta)}"
            lines.append(f"{prefix}\n{tag_preview}")
        return "\n".join(lines)

    @staticmethod
    def _cache_preview_next():
        return Script._format_cache_preview(tag_cache_manager.get_next_preview())

    @staticmethod
    def _cache_preview_delete_all():
        return Script._format_delete_preview(tag_cache_manager.preview_delete_all())

    @staticmethod
    def _cache_preview_delete_by_id(tag_id):
        stable_id = Script._parse_cache_id(tag_id)
        if stable_id is None:
            return "请输入搜索结果中的内部 ID"
        record = tag_cache_manager.get_record_by_id(stable_id)
        if not record:
            return f"未找到内部 ID {stable_id}"
        record = dict(record)
        record["position"] = f"ID {stable_id}"
        return Script._format_delete_preview(
            {
                "ok": True,
                "title": "按内部 ID 删除预览",
                "count": 1,
                "requested": 1,
                "sample": [record],
            }
        )

    @staticmethod
    def _cache_preview_delete_by_position_range(position_spec):
        if not str(position_spec or "").strip():
            return "请输入要预览删除的缓存序号或范围，例如 100-140, 150"
        return Script._format_delete_preview(tag_cache_manager.preview_delete_by_positions(position_spec))

    @staticmethod
    def _cache_preview_delete_by_tags(tags):
        if not str(tags or "").strip():
            return "请输入要预览删除的 tag，例如 comic,text,speech_bubble"
        return Script._format_delete_preview(tag_cache_manager.preview_delete_by_any_tags(tags))

    @staticmethod
    def _cache_get_next_and_set(
        loop_mode,
        tag_prompt_text,
        current_prompt,
        write_mode,
        prefer_natural=False,
    ):
        """从缓存顺序取出下一条并设置到提示词"""
        tags, idx, total = tag_cache_manager.get_next_tags_from_pool(
            loop=loop_mode,
            prefer_natural=prefer_natural,
        )
        if tags is None:
            if total == 0:
                return (
                    "缓存为空，请先批量爬取",
                    tag_cache_manager.get_status(),
                    current_prompt,
                    "",
                )
            else:
                return (
                    f"已到达缓存末尾 (索引 {idx}/{total})",
                    tag_cache_manager.get_status(),
                    current_prompt,
                    "",
                )
        status = tag_cache_manager.get_status()
        tag_payload = Script._combine_prompt(tag_prompt_text, tags)
        combined = Script._apply_write_mode(current_prompt, tag_payload, write_mode)
        return tags, status, combined, str(tag_cache_manager.tag_id_at_position(idx) or "")

    @staticmethod
    def _cache_jump_take(position, loop_mode, prefer_natural=False):
        result = tag_cache_manager.jump_to_position(position)
        if not result.get("ok"):
            return (
                result.get("message", "跳转失败"),
                Script._cache_refresh_status(),
                "",
            )
        return Script._cache_get_next(loop_mode, prefer_natural)

    @staticmethod
    def _cache_jump_take_and_set(
        position,
        loop_mode,
        tag_prompt_text,
        current_prompt,
        write_mode,
        prefer_natural=False,
    ):
        result = tag_cache_manager.jump_to_position(position)
        if not result.get("ok"):
            return (
                result.get("message", "跳转失败"),
                Script._cache_refresh_status(),
                current_prompt,
                "",
            )
        return Script._cache_get_next_and_set(
            loop_mode,
            tag_prompt_text,
            current_prompt,
            write_mode,
            prefer_natural,
        )

    @staticmethod
    def _natural_language_cache_status():
        status = tag_cache_manager.get_natural_prompt_status()
        return (
            f"主缓存共 {status['total']} 条；已预转换 {status['converted']} 条；"
            f"待转换 {status['missing']} 条"
        )

    @staticmethod
    def _save_natural_language_settings(
        preset,
        backend,
        endpoint_policy,
        endpoint,
        model,
        api_key,
        timeout,
        rag_enabled,
        rag_top_k,
        rag_min_percentile,
        rag_context_chars,
    ):
        settings = credentials_manager.save_natural_language_settings(
            preset,
            backend,
            endpoint_policy,
            endpoint,
            model,
            api_key,
            timeout,
            rag_enabled,
            rag_top_k,
            rag_min_percentile,
            rag_context_chars,
        )
        key_status = "已保存 API Key" if settings["api_key"] else "API Key 为空"
        return f"LLM 设置已保存；{key_status}。"

    @staticmethod
    def _clear_natural_language_settings():
        credentials_manager.clear_natural_language_settings()
        return (
            PRESET_KREA2,
            NATURAL_LANGUAGE_OFF,
            NATURAL_LANGUAGE_ENDPOINT_UNRESTRICTED,
            "",
            "",
            "",
            120,
            False,
            3,
            75,
            3000,
            "已清除保存的 LLM 设置和 API Key。",
        )

    @staticmethod
    def _format_natural_language_preview(result):
        if not result.get("matched", result.get("count")):
            return (
                f"{result.get('title', '自然语言预转换预览')}：未匹配到记录。"
                f"当前活动缓存共 {result.get('total', 0)} 条。"
            )
        if not result.get("selected", result.get("count")):
            return (
                f"{result.get('title', '自然语言预转换预览')}：输入命中 "
                f"{result.get('matched', 0)} 条，但这些记录都已有自然语言结果；"
                "关闭“只转换尚未转换的记录”可覆盖转换。"
            )
        lines = [
            (
                f"{result.get('title', '自然语言预转换预览')}："
                f"输入命中 {result.get('matched', result.get('count', 0))} / "
                f"{result.get('requested', 0)} 条；"
                f"其中已有结果 {result.get('converted', 0)} 条，"
                f"待转换 {result.get('missing', 0)} 条；"
                f"本次将处理 {result.get('selected', result.get('count', 0))} 条。"
            )
        ]
        for record in result.get("sample", []):
            raw = str(record.get("tags_prompt") or "").replace("\n", " ")
            natural = str(record.get("natural_prompt") or "").replace("\n", " ")
            lines.append(
                f"#{record.get('position')} [ID {record.get('id')}] "
                f"原始：{raw[:180]}"
            )
            if natural:
                lines.append(f"    已转换：{natural[:220]}")
        return "\n".join(lines)

    @staticmethod
    def _cache_preview_natural_conversion(position_spec, only_missing=True):
        if not str(position_spec or "").strip():
            return "请输入当前活动缓存的可见序号或范围，例如 1-100,205-240。"
        result = tag_cache_manager.preview_natural_conversion(
            position_spec,
            only_missing=only_missing,
        )
        return Script._format_natural_language_preview(result)

    @staticmethod
    def _cache_batch_convert_natural_unlocked(
        position_spec,
        only_missing,
        preset,
        backend,
        endpoint_policy,
        endpoint,
        model,
        api_key,
        timeout,
        rag_enabled,
        rag_top_k,
        rag_min_percentile,
        rag_context_chars,
        cancel_event=None,
    ):
        cancel_event = cancel_event or _natural_batch_cancel
        if not str(position_spec or "").strip():
            yield (
                "请输入当前活动缓存的可见序号或范围，例如 1-100,205-240。",
                Script._natural_language_cache_status(),
            )
            return
        if backend == NATURAL_LANGUAGE_OFF:
            yield (
                "请选择 Ollama（本地）或 OpenAI 兼容 LLM 后再开始批量转换。",
                Script._natural_language_cache_status(),
            )
            return
        if not str(model or "").strip():
            yield (
                "请填写模型名称。",
                Script._natural_language_cache_status(),
            )
            return
        try:
            saved_settings = credentials_manager.save_natural_language_settings(
                preset,
                backend,
                endpoint_policy,
                endpoint,
                model,
                api_key,
                timeout,
                rag_enabled,
                rag_top_k,
                rag_min_percentile,
                rag_context_chars,
            )
        except OSError as error:
            yield (
                f"批量转换未开始：保存 LLM 设置失败：{error}",
                Script._natural_language_cache_status(),
            )
            return
        records = tag_cache_manager.get_records_by_position_spec(
            position_spec,
            only_missing=only_missing,
        )
        if not records:
            yield (
                "所选范围没有待转换记录；可关闭“只转换尚未转换的记录”后覆盖已有结果。",
                Script._natural_language_cache_status(),
            )
            return

        try:
            backup_path = tag_cache_manager.backup_db("natural_batch")
        except Exception as error:
            yield (
                f"批量转换未开始：数据库备份失败：{error}",
                Script._natural_language_cache_status(),
            )
            return
        config = NaturalLanguageConfig(
            backend=saved_settings["backend"],
            endpoint=saved_settings["endpoint"],
            model=saved_settings["model"],
            api_key=saved_settings["api_key"],
            timeout=saved_settings["timeout"],
            preset=saved_settings["preset"],
            endpoint_policy=saved_settings["endpoint_policy"],
            few_shot_max_chars=saved_settings.get("rag_context_chars", 3000),
        )
        rag_enabled = bool(saved_settings.get("rag_enabled", False))
        rag_top_k = int(saved_settings.get("rag_top_k", 3))
        rag_min_percentile = (
            float(saved_settings.get("rag_min_percentile", 75)) / 100.0
        )
        rag_warning = ""
        try:
            rag_candidates = (
                tag_cache_manager.get_prompt_rag_candidates(
                    preset=config.preset,
                    min_score_percentile=rag_min_percentile,
                )
                if rag_enabled
                else []
            )
        except Exception as error:
            rag_candidates = []
            rag_warning = "；RAG 候选读取失败，已回退 Zero-Shot"
            logger.warning("Prompt RAG candidate loading failed: %s", error)
        rag_queries_with_examples = 0
        rag_examples_used = 0

        def retrieve_examples(original):
            nonlocal rag_queries_with_examples, rag_examples_used
            examples = tag_cache_manager.select_prompt_rag_examples(
                original,
                rag_candidates,
                limit=rag_top_k,
            )
            injected_count = len(
                CachedTagNaturalLanguageConverter._few_shot_messages(
                    examples,
                    max_chars=config.few_shot_max_chars,
                )
            ) // 2
            if injected_count:
                rag_queries_with_examples += 1
                rag_examples_used += injected_count
            return examples

        converter = CachedTagNaturalLanguageConverter()
        pending_updates = []
        processed = 0
        saved = 0
        source_changed = 0
        source_deleted = 0
        reused = 0
        timeout_skipped = 0
        transient_skipped = 0
        consecutive_timeouts = 0
        max_consecutive_timeouts = 6
        update_batch_size = 10

        def flush_pending():
            nonlocal saved, source_changed, source_deleted
            if not pending_updates:
                return
            update_result = tag_cache_manager.update_natural_prompts(
                pending_updates,
                preset=config.preset,
                model=config.model,
                only_missing=only_missing,
                converter_version=NATURAL_LANGUAGE_CONVERTER_VERSION,
            )
            saved += update_result["updated"]
            source_changed += update_result["source_changed"]
            source_deleted += update_result["source_deleted"]
            pending_updates.clear()

        try:
            yield (
                f"已保存 LLM 设置和服务地址；已选择 {len(records)} 条整记录，"
                f"数据库备份完成；本地 RAG 候选 {len(rag_candidates)} 条"
                f"{rag_warning}；开始调用模型。",
                Script._natural_language_cache_status(),
            )
            for index, original, converted, error, was_reused in iter_cached_tag_conversions(
                [record["tags_prompt"] for record in records],
                config,
                converter,
                should_cancel=cancel_event.is_set,
                example_provider=retrieve_examples if rag_enabled else None,
            ):
                processed = index + 1
                if error:
                    flush_pending()
                    if is_timeout_conversion_error(error):
                        timeout_skipped += 1
                        consecutive_timeouts += 1
                        if consecutive_timeouts >= max_consecutive_timeouts:
                            yield (
                                (
                                    f"批量转换在第 {processed}/{len(records)} 条停止："
                                    f"已连续 {consecutive_timeouts} 条记录单次请求超时。\n"
                                    f"本次已保存 {saved} 条；超时跳过 {timeout_skipped} 条；"
                                    f"源记录变化 {source_changed} 条，已删除 {source_deleted} 条。备份：{backup_path}"
                                ),
                                Script._natural_language_cache_status(),
                            )
                            return
                        yield (
                            (
                                f"第 {processed}/{len(records)} 条单次请求超时，"
                                f"已跳过并继续；连续超时 {consecutive_timeouts}/"
                                f"{max_consecutive_timeouts}；已保存 {saved} 条。"
                            ),
                            Script._natural_language_cache_status(),
                        )
                        continue
                    if is_retryable_conversion_error(error):
                        transient_skipped += 1
                        consecutive_timeouts = 0
                        yield (
                            (
                                f"Record {processed}/{len(records)} failed after one request; "
                                f"skipped and continuing. Saved {saved}.\n{error}"
                            ),
                            Script._natural_language_cache_status(),
                        )
                        continue
                    yield (
                        (
                            f"批量转换在第 {processed}/{len(records)} 条停止：{error}\n"
                            f"已保存 {saved} 条；源记录变化 {source_changed} 条，已删除 {source_deleted} 条；"
                            f"已完成的结果仍保留。备份：{backup_path}"
                        ),
                        Script._natural_language_cache_status(),
                    )
                    return
                consecutive_timeouts = 0
                if not converted:
                    continue
                if was_reused:
                    reused += 1
                record = records[index]
                pending_updates.append(
                    {
                        "id": record["id"],
                        "source_tags_prompt": original,
                        "natural_prompt": converted,
                    }
                )
                if len(pending_updates) >= update_batch_size:
                    flush_pending()
                if cancel_event.is_set():
                    flush_pending()
                    yield (
                        (
                            f"批量转换已取消：完成 {processed}/{len(records)} 条，"
                            f"已保存 {saved} 条，源记录变化 {source_changed} 条，删除 {source_deleted} 条。"
                            f"备份：{backup_path}"
                        ),
                        Script._natural_language_cache_status(),
                    )
                    return
                if processed % 5 == 0:
                    yield (
                        (
                            f"正在转换：{processed}/{len(records)}；已保存 {saved} 条；"
                            f"复用相同整条 Tag 的结果 {reused} 条。"
                        ),
                        Script._natural_language_cache_status(),
                    )

            flush_pending()
            yield (
                (
                    f"批量预转换完成：选择 {len(records)} 条，保存 {saved} 条，"
                    f"复用相同整条 Tag 的结果 {reused} 条，"
                    f"RAG 命中 {rag_queries_with_examples} 条并注入 {rag_examples_used} 个样例，"
                    f"超时跳过 {timeout_skipped} 条，"
                    f"源记录变化 {source_changed} 条，已删除 {source_deleted} 条。备份：{backup_path}"
                ),
                Script._natural_language_cache_status(),
            )
        except NaturalLanguageBatchCancelled:
            flush_pending()
            yield (
                (
                    f"Batch conversion cancelled at {processed}/{len(records)}; "
                    f"saved {saved}, timed out {timeout_skipped}, "
                    f"other transient failures {transient_skipped}. Backup: {backup_path}"
                ),
                Script._natural_language_cache_status(),
            )
        finally:
            try:
                flush_pending()
            finally:
                converter.close()

    @staticmethod
    def _cache_batch_convert_natural(
        position_spec,
        only_missing,
        preset,
        backend,
        endpoint_policy,
        endpoint,
        model,
        api_key,
        timeout,
        rag_enabled,
        rag_top_k,
        rag_min_percentile,
        rag_context_chars,
        cancel_id="",
    ):
        event_key, cancel_event = _natural_cancel_event(cancel_id)
        cancel_event.clear()
        try:
            yield from Script._cache_batch_convert_natural_unlocked(
                position_spec,
                only_missing,
                preset,
                backend,
                endpoint_policy,
                endpoint,
                model,
                api_key,
                timeout,
                rag_enabled,
                rag_top_k,
                rag_min_percentile,
                rag_context_chars,
                cancel_event,
            )
        finally:
            cancel_event.clear()
            with _natural_batch_cancel_lock:
                if _natural_batch_cancel_events.get(event_key) is cancel_event:
                    _natural_batch_cancel_events.pop(event_key, None)

    @staticmethod
    def _cancel_natural_batch(cancel_id=""):
        _key, event = _natural_cancel_event(cancel_id)
        event.set()
        return "已请求取消；当前模型请求返回后将保存已完成结果并停止。"

    @staticmethod
    def _cache_clear_natural_conversion(position_spec):
        return Script._cache_clear_natural_conversion_unlocked(position_spec)

    @staticmethod
    def _cache_clear_natural_conversion_unlocked(position_spec):
        if not str(position_spec or "").strip():
            return (
                "请输入要清除自然语言结果的可见序号或范围。",
                Script._natural_language_cache_status(),
            )
        records = tag_cache_manager.get_records_by_position_spec(
            position_spec,
            only_missing=False,
        )
        converted = [
            record
            for record in records
            if str(record.get("natural_prompt") or "").strip()
        ]
        if not converted:
            return (
                "所选范围没有已保存的自然语言结果。",
                Script._natural_language_cache_status(),
            )
        try:
            backup_path = tag_cache_manager.backup_db("natural_clear")
        except Exception as error:
            return (
                f"未清除任何结果：数据库备份失败：{error}",
                Script._natural_language_cache_status(),
            )
        cleared = tag_cache_manager.clear_natural_prompts_by_ids(
            record["id"] for record in converted
        )
        return (
            f"已清除 {cleared} 条自然语言结果；原始 Tag 未修改。备份：{backup_path}",
            Script._natural_language_cache_status(),
        )

    @staticmethod
    def _cache_delete_by_position_range(position_spec):
        if not str(position_spec or "").strip():
            return "请输入要删除的缓存序号或范围，例如 100-140, 150", tag_cache_manager.get_status()
        deleted, requested = tag_cache_manager.delete_by_position_spec(position_spec)
        return f"已请求 {requested} 个序号，删除 {deleted} 条缓存", tag_cache_manager.get_status()

    @staticmethod
    def _cache_export(file_format):
        try:
            result = tag_cache_manager.export_records(file_format)
            scope = f"{result['count']}/{result['total_count']} 条" if result.get("truncated") else f"{result['count']} 条"
            return f"已导出 {scope} {result['format'].upper()}：{result['path']}"
        except Exception as e:
            return f"导出失败: {e}"

    @staticmethod
    def _cache_import(file_path, append_mode, dedupe):
        result = tag_cache_manager.import_records(file_path, append=append_mode, dedupe=dedupe)
        if not result.get("ok"):
            return result.get("message", "导入失败"), tag_cache_manager.get_status()
        backup_note = f"，覆盖前备份: {result.get('backup_path')}" if result.get("backup_path") else ""
        return (
            f"导入完成: 写入 {result['inserted']} 条，补全 {result.get('enriched', 0)} 条，"
            f"重复跳过 {result['skipped_duplicate']} 条，总计 {result['total']} 条{backup_note}",
            tag_cache_manager.get_status(),
        )

    @staticmethod
    def _cache_import_payload(payload, append_mode=True, dedupe=True):
        """Import a Collector JSON batch directly from a textbox payload."""
        try:
            payload_text = str(payload or "")
            data = json.loads(payload_text)
            if not isinstance(data, dict) or data.get("schema_version") != "prompt_batch.v1":
                return "Collector 批次 schema_version 无效", tag_cache_manager.get_status()
            loaded = tag_cache_manager.normalize_prompt_batch_payload(data)
            if not loaded.get("ok"):
                return loaded.get("message", "Collector 批次无效"), tag_cache_manager.get_status()
            records = loaded["records"]
            preview = []
            for index, record in enumerate(records, 1):
                if len(preview) < 8:
                    preview.append(f"{index}: {record['tags_prompt'][:120]}")
            result = (tag_cache_manager.append_records(records, dedupe=dedupe)
                      if append_mode else tag_cache_manager.save_records(records, dedupe=dedupe, backup_reason="collector_import"))
            return (
                "Collector 批次导入完成\n" + "\n".join(preview)
                + f"\n写入 {result.get('inserted', 0)} 条，补全 {result.get('enriched', 0)} 条"
            ), tag_cache_manager.get_status()
        except Exception as error:
            return f"Collector 批次解析失败: {error}", tag_cache_manager.get_status()

    @staticmethod
    def _format_import_preview(result):
        if not result.get("ok"):
            return result.get("message", "导入预检失败")
        mode = "追加" if result.get("append") else "覆盖"
        lines = [
            f"导入预检 ({mode}): 文件记录 {result.get('source_count', 0)} 条，预计写入 {result.get('inserted', 0)} 条，补全 {result.get('enriched', 0)} 条",
            f"空记录跳过 {result.get('skipped_empty', 0)} 条，重复跳过 {result.get('skipped_duplicate', 0)} 条",
            f"当前总数 {result.get('current_total', 0)} 条，导入后预计 {result.get('estimated_total', 0)} 条",
        ]
        if result.get("path"):
            lines.append(f"文件: {result['path']}")
        if result.get("sample"):
            lines.append("可写入样本：")
            for index, record in enumerate(result["sample"], start=1):
                tags = str(record.get("tags_prompt") or "")
                tag_preview = tags[:140] + ("..." if len(tags) > 140 else "")
                meta = []
                if record.get("booru"):
                    meta.append(str(record["booru"]))
                if record.get("post_id"):
                    meta.append(f"post {record['post_id']}")
                prefix = f"{index}."
                if meta:
                    prefix += f" {' | '.join(meta)}"
                lines.append(f"{prefix} {tag_preview}")
        return "\n".join(lines)

    @staticmethod
    def _cache_import_preflight(file_path, append_mode, dedupe):
        return Script._format_import_preview(tag_cache_manager.preview_import_records(file_path, append=append_mode, dedupe=dedupe))

    def ui(self, is_img2img):
        default_booru = "safebooru"
        saved_natural_settings = credentials_manager.get_natural_language_settings()
        saved_natural_preset = saved_natural_settings.get("preset", PRESET_KREA2)
        if saved_natural_preset not in NATURAL_LANGUAGE_PRESETS:
            saved_natural_preset = PRESET_KREA2
        saved_natural_backend = saved_natural_settings.get(
            "backend", NATURAL_LANGUAGE_OFF
        )
        if saved_natural_backend not in NATURAL_LANGUAGE_BACKENDS:
            saved_natural_backend = NATURAL_LANGUAGE_OFF
        saved_natural_policy = saved_natural_settings.get(
            "endpoint_policy", NATURAL_LANGUAGE_ENDPOINT_UNRESTRICTED
        )
        if saved_natural_policy not in _natural_endpoint_policy_choices:
            saved_natural_policy = NATURAL_LANGUAGE_ENDPOINT_UNRESTRICTED
        try:
            saved_natural_timeout = min(
                300.0,
                max(1.0, float(saved_natural_settings.get("timeout", 120))),
            )
        except (TypeError, ValueError):
            saved_natural_timeout = 120.0
        saved_rag_enabled = saved_natural_settings.get("rag_enabled", False)
        if isinstance(saved_rag_enabled, str):
            saved_rag_enabled = saved_rag_enabled.strip().lower() not in {
                "0", "false", "no", "off"
            }
        else:
            saved_rag_enabled = bool(saved_rag_enabled)
        try:
            saved_rag_top_k = max(
                1,
                min(5, int(float(saved_natural_settings.get("rag_top_k", 3)))),
            )
        except (TypeError, ValueError, OverflowError):
            saved_rag_top_k = 3
        try:
            saved_rag_min_percentile = max(
                0.0,
                min(
                    100.0,
                    float(saved_natural_settings.get("rag_min_percentile", 75)),
                ),
            )
        except (TypeError, ValueError, OverflowError):
            saved_rag_min_percentile = 75.0
        try:
            saved_rag_context_chars = max(
                0,
                min(
                    12000,
                    int(float(saved_natural_settings.get("rag_context_chars", 3000))),
                ),
            )
        except (TypeError, ValueError, OverflowError):
            saved_rag_context_chars = 3000
        saved_natural_has_api_key = bool(
            str(saved_natural_settings.get("api_key", "")).strip()
        )
        # Determine initial Gelbooru credential visibility based on saved credentials
        has_saved = credentials_manager.has_credentials('gelbooru')
        initial_api_key_visible = not has_saved
        initial_user_id_visible = not has_saved
        initial_status_value = "✓ Credentials are configured on the server" if has_saved else ""
        initial_clear_visible = has_saved

        row_container = self.prompt_row[is_img2img] or gr.Row()
        input_row = self.input_row[is_img2img] or gr.Row()
        action_row = self.action_row[is_img2img] or gr.Row()
        with row_container:
            with input_row:
                with gr.Column(scale=2, min_width=220):
                    tags = gr.Textbox(lines=1, label="Tags to Search (Pre)", elem_id="ranbooru_tags", elem_classes=["ranbooru-input"])
                with gr.Column(scale=8):
                    tag_prompt_input = gr.Textbox(lines=3, label="Tag Prompt", elem_id="ranbooru_tag_prompt", elem_classes=["ranbooru-input"])
            with action_row:
                with gr.Column(scale=2, min_width=220):
                    generate_prompt_btn = gr.Button("生成提示词", elem_id="ranbooru_generate_prompt", elem_classes=["ranbooru-primary-action"])
                with gr.Column(scale=8):
                    with gr.Accordion(label="Ranbooru", open=False, elem_id="ranbooru_online_workspace", elem_classes=["ranbooru-panel"]):
                        enabled = gr.Checkbox(label="Enabled", value=False)
                        with gr.Row():
                            with gr.Column(scale=1):
                                booru = gr.Dropdown(["safebooru", "rule34", "danbooru", "gelbooru", 'konachan', 'yande.re', 'aibooru', 'xbooru', 'e621'], label="Booru", value=default_booru)
                                tag_categories = gr.CheckboxGroup(
                                    ["general", "character", "copyright", "artist", "meta"],
                                    value=["general", "character", "copyright"],
                                    label="Tag Categories (Danbooru/Safebooru/AIBooru/e621)",
                                    visible=default_booru in ['danbooru', 'safebooru', 'aibooru', 'e621']
                                )
                                max_pages = gr.Number(label="Max Pages", minimum=1, maximum=9999, value=100, step=1, precision=0)
                                gr.Markdown("""## Post""")
                                post_id = gr.Textbox(lines=1, label="Post ID")
                                gr.Markdown("""## Tags""")
                                remove_tags = gr.Textbox(lines=1, label="Tags to Remove (Post)")
                                with gr.Group():
                                    with gr.Group():
                                        prompt_output = gr.Textbox(lines=3, label="提示词输出")
                            with gr.Column(scale=1):
                                mature_rating = gr.Radio(list(RATINGS[default_booru]), label="Mature Rating", value="All")
                                remove_bad_tags = gr.Checkbox(label="Remove bad tags", value=True)
                                shuffle_tags = gr.Checkbox(label="Shuffle tags", value=True)
                                change_dash = gr.Checkbox(label='Convert "_" to spaces', value=False)
                                same_prompt = gr.Checkbox(label="Use same prompt for all images", value=False)
                                fringe_benefits = gr.Checkbox(
                                    label="Fringe Benefits / Rule34 animated fallback",
                                    value=(default_booru == 'gelbooru'),
                                    visible=(default_booru in ('gelbooru', 'rule34')),
                                )
                                with gr.Group(visible=False) as gelbooru_credentials_group:
                                    gr.Markdown("### API Credentials")
                                    api_key = gr.Textbox(
                                        lines=1, label="API Key", placeholder="Enter your API key",
                                        type="password", visible=initial_api_key_visible, value=""
                                    )
                                    user_id = gr.Textbox(
                                        lines=1, label="User ID", placeholder="Enter your user ID",
                                        visible=initial_user_id_visible, value=""
                                    )
                                    save_credentials = gr.Checkbox(label="Save credentials", value=False, visible=True)
                                    credentials_status = gr.Textbox(
                                        label="Status", interactive=False,
                                        visible=True, value=initial_status_value
                                    )
                                    clear_credentials_btn = gr.Button(
                                        "Clear saved credentials", visible=initial_clear_visible
                                    )
                                limit_tags = gr.Slider(value=1.0, label="Limit tags", minimum=0.05, maximum=1.0, step=0.05)
                                max_tags = gr.Slider(value=100, label="Max tags", minimum=1, maximum=100, step=1)
                                change_background = gr.Radio(["Don't Change", "Add Background", "Remove Background", "Remove All"], label="Change Background", value="Don't Change")
                                change_color = gr.Radio(["Don't Change", "Colored", "Limited Palette", "Monochrome"], label="Change Color", value="Don't Change")
                            sorting_order = gr.Radio(["Random", "High Score", "Low Score"], label="Sorting Order", value="Random")
                        booru.change(get_available_ratings, booru, mature_rating)
                        booru.change(show_fringe_benefits, booru, fringe_benefits)
                        booru.change(self.show_gelbooru_api_fields, booru, gelbooru_credentials_group)
                        booru.change(
                            fn=lambda b: gr.update(visible=b in ['danbooru', 'safebooru', 'aibooru', 'e621']),
                            inputs=[booru],
                            outputs=[tag_categories]
                        )

                        with gr.Accordion("Img2Img", open=False):
                            use_img2img = gr.Checkbox(label="Use img2img", value=False)
                            use_ip = gr.Checkbox(label="Send to Controlnet", value=False)
                            denoising = gr.Slider(value=0.75, label="Denoising", minimum=0.05, maximum=1.0, step=0.05)
                            use_last_img = gr.Checkbox(label="Use last image as img2img", value=False)
                            crop_center = gr.Checkbox(label="Crop Center", value=False)
                            use_deepbooru = gr.Checkbox(label="Use Deepbooru", value=False, interactive=has_deepbooru())
                            type_deepbooru = gr.Radio(["Add Before", "Add After", "Replace"], label="Deepbooru Tags Position", value="Add Before")
                        with gr.Accordion("File", open=False):
                            use_search_txt = gr.Checkbox(label="Use tags_search.txt", value=False)
                            choose_search_txt = gr.Dropdown(self.get_files(user_search_dir), label="Choose tags_search.txt", value="")
                            search_refresh_btn = gr.Button("Refresh")
                            use_remove_txt = gr.Checkbox(label="Use tags_remove.txt", value=False)
                            choose_remove_txt = gr.Dropdown(self.get_files(user_remove_dir), label="Choose tags_remove.txt", value="")
                            remove_refresh_btn = gr.Button("Refresh")
                        with gr.Accordion("Extra", open=False):
                            mix_prompt = gr.Checkbox(label="Mix prompts", value=False)
                            mix_amount = gr.Slider(value=2, label="Mix amount", minimum=2, maximum=10, step=1)
                            chaos_mode = gr.Radio(["None", "Chaos", "Less Chaos"], label="Chaos Mode", value="None")
                            chaos_amount = gr.Slider(value=0.5, label="Chaos Amount %", minimum=0.1, maximum=1, step=0.05)
                            negative_mode = gr.Radio(["None", "Negative"], label="Negative Mode", value="None")
                            use_same_seed = gr.Checkbox(label="Use same seed for all pictures", value=False)
                            use_cache = gr.Checkbox(label="Use cache", value=True)

                    # ─── 本地缓存工作区 ────────────────────────────────────────
                    with gr.Accordion(
                        "本地缓存工作区",
                        open=False,
                        elem_id="ranbooru_cache_workspace",
                        elem_classes=["ranbooru-cache-workspace"],
                    ):
                        with gr.Tabs(elem_id="ranbooru_cache_tabs", elem_classes=["ranbooru-cache-tabs"]):
                            with gr.Tab("缓存采集", elem_id="ranbooru_tab_collect"):
                                gr.Markdown("### 采集条件")
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_status_display = gr.Textbox(
                                        elem_id="ranbooru_cache_status",
                                        label="缓存状态", value=tag_cache_manager.get_status(),
                                        interactive=False, lines=1
                                    )
                                    cache_refresh_status_btn = gr.Button("刷新状态")

                                gr.Markdown("#### 爬取设置")
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_pages = gr.Number(label="爬取页数", minimum=1, maximum=100, value=5, step=1, precision=0)
                                    cache_start_page = gr.Number(label="从第几页开始缓存", minimum=1, maximum=100000, value=1, step=1, precision=0)
                                    cache_append_mode = gr.Checkbox(label="追加模式（不覆盖已有缓存）", value=True)
                                    cache_dedupe = gr.Checkbox(label="强制入库去重（完全相同 Tag 必删）", value=True, interactive=False)
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_min_score_enabled = gr.Checkbox(label="启用最低分过滤", value=False)
                                    cache_min_score = gr.Number(label="最低 Score", value=0, step=1, precision=0)
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_keep_all_tags = gr.Textbox(
                                        label="缓存必须全部包含",
                                        placeholder="例如: kafuu_chino",
                                        lines=1,
                                    )
                                    cache_keep_any_tags = gr.Textbox(
                                        label="缓存必须任意包含",
                                        placeholder="例如: 1girl,2girls",
                                        lines=1,
                                    )
                                cache_exclude_tags = gr.Textbox(
                                    label="缓存排除 Tag（命中任意一个就不入库）",
                                    placeholder="例如: comic,text,speech_bubble,english_text",
                                    lines=1,
                                )
                                cache_fetch_btn = gr.Button("开始批量爬取", variant="primary")
                                cache_fetch_result = gr.Textbox(label="爬取结果", interactive=False, lines=2)

                            with gr.Tab("浏览与联动", elem_id="ranbooru_tab_browse"):
                                gr.Markdown("### 浏览与使用")
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_loop_mode = gr.Checkbox(label="循环播放（到末尾后从头开始）", value=True)
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_next_btn = gr.Button("取出下一条")
                                    cache_next_set_btn = gr.Button("取出并设置到提示词", variant="primary")
                                    cache_prompt_write_mode = gr.Dropdown(
                                        ["追加到后面", "追加到前面", "替换", "只输出"],
                                        label="写入模式",
                                        value="追加到后面"
                                    )
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_lookup_id = gr.Textbox(label="按缓存序号取 Tag", placeholder="例如: 110", lines=1)
                                    cache_lookup_id_btn = gr.Button("按序号取出")
                                    cache_lookup_id_set_btn = gr.Button("按序号写入 Tag Prompt")
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_jump_position = gr.Number(label="跳转到第几条", minimum=1, step=1, precision=0)
                                    cache_jump_position_btn = gr.Button("跳转到该条")
                                    cache_jump_take_btn = gr.Button("跳转并取出")
                                    cache_jump_take_set_btn = gr.Button("跳转并写入 Tag Prompt", variant="primary")
                                cache_jump_result = gr.Textbox(label="跳转结果", interactive=False, lines=1)
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_preview_next_btn = gr.Button("预览下一条")
                                cache_preview_output = gr.Textbox(label="下一条预览", interactive=False, lines=3)
                                cache_next_output = gr.Textbox(
                                    elem_id="ranbooru_cache_current_prompt",
                                    label="当前取出的 Tag / 转换后 Prompt",
                                    interactive=False,
                                    lines=3,
                                )
                                cache_current_record_id = gr.State("")
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_send_prompt_studio_btn = gr.Button("发送到 LLM 提示词工作室")
                                    cache_process_prompt_studio_btn = gr.Button(
                                        "使用 LLM 处理并缓存",
                                        variant="primary",
                                    )
                                cache_prompt_studio_result = gr.Textbox(
                                    label="LLM 提示词工作室处理结果",
                                    interactive=False,
                                    lines=4,
                                )
                                cache_prompt_studio_status = gr.Textbox(
                                    elem_id="ranbooru_llm_handoff_status",
                                    label="LLM 提示词工作室联动状态",
                                    interactive=False,
                                    lines=2,
                                )

                                gr.Markdown("### 生成使用方式")
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    use_local_cache_gen = gr.Checkbox(label="生成时使用此缓存", value=False)
                                    use_local_cache_loop = gr.Checkbox(label="生成时循环读取 (到末尾自动重头)", value=True)
                                    use_preconverted_cache_prompt = gr.Checkbox(
                                        label="优先使用已预转换的自然语言 Prompt",
                                        value=True,
                                    )
                            with gr.Tab("自然语言与 RAG", elem_id="ranbooru_tab_natural"):
                                gr.Markdown(
                                    "按主缓存的**可见序号**选择几十或"
                                    "几百条记录；每条记录的整个 `tags_prompt` 会一次性转换并保存到数据库的"
                                    "独立自然语言字段，原始 Tag 不会被覆盖。生成和手动写入时只读取已保存"
                                    "结果，未转换的记录自动回退为原始 Tag，不会临时调用模型。\n\n"
                                    "Krea 2 预设采用紧凑自然语言：优先描述媒介、主体、动作、场景、构图、"
                                    "时间/光线与单一风格锚点，并去掉 masterpiece、8k 等空泛质量词。\n\n"
                                    "启用本地 RAG 后，会从有效的已转换缓存中检索相似且高分的记录，"
                                    "作为 Few-Shot 示例并发送到当前模型服务；没有可用样例时自动使用 Zero-Shot。\n\n"
                                    "仅使用你信任的模型服务：OpenAI 兼容模式会把选中的缓存 Tag "
                                    "发送到所填地址；共享或公网 WebUI 应限制此面板的访问。"
                                )
                                cache_natural_language_status = gr.Textbox(
                                    label="预转换状态",
                                    value=self._natural_language_cache_status(),
                                    interactive=False,
                                    lines=1,
                                    elem_classes=["ranbooru-status"],
                                )
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_natural_language_positions = gr.Textbox(
                                        label="选择可见序号 / 范围",
                                        placeholder="例如：1-100,205-240,301",
                                        lines=1,
                                    )
                                    cache_natural_language_only_missing = gr.Checkbox(
                                        label="只转换尚未转换的记录",
                                        value=True,
                                    )
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_prompt_rag_enabled = gr.Checkbox(
                                        label="启用本地高分 Prompt RAG / Few-Shot",
                                        value=saved_rag_enabled,
                                    )
                                    cache_prompt_rag_top_k = gr.Number(
                                        label="Few-Shot 样例数",
                                        minimum=1,
                                        maximum=5,
                                        value=saved_rag_top_k,
                                        step=1,
                                        precision=0,
                                    )
                                    cache_prompt_rag_min_percentile = gr.Number(
                                        label="RAG 候选最低分位（%）",
                                        minimum=0,
                                        maximum=100,
                                        value=saved_rag_min_percentile,
                                        step=1,
                                        precision=0,
                                    )
                                    cache_prompt_rag_context_chars = gr.Number(
                                        label="Few-Shot 上下文字符预算",
                                        minimum=0,
                                        maximum=12000,
                                        value=saved_rag_context_chars,
                                        step=250,
                                        precision=0,
                                    )
                                cache_natural_language_preset = gr.Dropdown(
                                    NATURAL_LANGUAGE_PRESETS,
                                    label="自然语言预设",
                                    value=saved_natural_preset,
                                )
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_natural_language_backend = gr.Dropdown(
                                        NATURAL_LANGUAGE_BACKENDS,
                                        label="转换方式",
                                        value=saved_natural_backend,
                                    )
                                    cache_natural_language_endpoint_policy = gr.Dropdown(
                                        _natural_endpoint_policy_choices,
                                        label="服务地址兼容策略",
                                        value=saved_natural_policy,
                                        info=(
                                            "unrestricted：普通 HTTP 客户端模式，兼容系统代理、Fake-IP 和任意地址（推荐）；"
                                            "allow_private：允许本机、LAN 和 Fake-IP，但仍校验 DNS/IP；"
                                            "default：仅允许本机 Ollama 和公网服务；"
                                            "public_only：仅公网。"
                                        ),
                                    )
                                    cache_natural_language_timeout = gr.Number(
                                        label="请求超时（秒）",
                                        minimum=1,
                                        maximum=300,
                                        value=saved_natural_timeout,
                                        step=1,
                                        precision=0,
                                    )
                                cache_natural_language_endpoint = gr.Textbox(
                                    label="模型服务地址（留空使用后端默认地址）",
                                    value=str(saved_natural_settings.get("endpoint", "")),
                                    placeholder=(
                                        "Ollama: http://127.0.0.1:11434；"
                                        "OpenAI 兼容: https://api.openai.com/v1"
                                    ),
                                    lines=1,
                                )
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_natural_language_model = gr.Textbox(
                                        label="模型名称",
                                        value=str(saved_natural_settings.get("model", "")),
                                        placeholder="例如 qwen3:8b 或服务端提供的模型 ID",
                                        lines=1,
                                    )
                                    cache_natural_language_api_key = gr.Textbox(
                                        label=(
                                            "API Key（已保存，无需重填）"
                                            if saved_natural_has_api_key
                                            else "API Key（Ollama 可留空）"
                                        ),
                                        type="password",
                                        value="",
                                        placeholder=(
                                            "已保存到服务器；留空继续使用，输入新值可替换。"
                                            if saved_natural_has_api_key
                                            else "输入后会与服务地址等 LLM 设置一起保存。"
                                        ),
                                        lines=1,
                                    )
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_natural_language_save_settings_btn = gr.Button(
                                        "保存 LLM 设置（含 API Key）"
                                    )
                                    cache_natural_language_clear_settings_btn = gr.Button(
                                        "清除保存的 LLM 设置"
                                    )
                                cache_natural_language_settings_status = gr.Textbox(
                                    label="LLM 设置",
                                    value=(
                                        "已加载保存的 LLM 设置；API Key 无需重填。"
                                        if saved_natural_has_api_key
                                        else "已加载保存的 LLM 设置。"
                                        if saved_natural_settings
                                        else "尚未保存 LLM 设置。"
                                    ),
                                    interactive=False,
                                    lines=1,
                                )
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_natural_language_preview_btn = gr.Button(
                                        "预览所选缓存"
                                    )
                                    cache_natural_language_convert_btn = gr.Button(
                                        "批量整条转换并保存",
                                        variant="primary",
                                    )
                                    cache_natural_language_cancel_btn = gr.Button(
                                        "取消批量转换",
                                        variant="stop",
                                    )
                                    cache_natural_cancel_id = gr.State(lambda: uuid.uuid4().hex)
                                    cache_natural_language_clear_btn = gr.Button(
                                        "清除所选已转换结果"
                                    )
                                    cache_natural_language_refresh_btn = gr.Button(
                                        "刷新状态"
                                    )
                                cache_natural_language_preview = gr.Textbox(
                                    label="预览 / 执行结果",
                                    interactive=False,
                                    lines=8,
                                )

                            with gr.Tab("维护与导入导出", elem_id="ranbooru_tab_maintenance"):
                                gr.Markdown("### 搜索与维护")
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_search_keyword = gr.Textbox(label="搜索语法", placeholder="例如: +1girl +blue_eyes -2girls -text", lines=1)
                                    cache_search_btn = gr.Button("搜索")
                                cache_search_results = gr.Dataframe(
                                    headers=["主缓存序号", "Booru", "Post ID", "Score", "Rating", "Tags", "内部 ID"],
                                    label="搜索结果",
                                    interactive=False,
                                    wrap=True,
                                    elem_classes=["ranbooru-table"],
                                )
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_select_id = gr.Number(
                                        label="搜索结果内部 ID",
                                        minimum=1,
                                        step=1,
                                        precision=0,
                                    )
                                    cache_fill_btn = gr.Button("填充到输出")
                                    cache_preview_delete_id_btn = gr.Button("预览此条")
                                    cache_delete_id_btn = gr.Button("删除此条", variant="stop")
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_reset_btn = gr.Button("重置索引")
                                    cache_backup_btn = gr.Button("备份缓存")
                                    cache_restore_deleted_btn = gr.Button("撤销上一次删除")
                                    cache_preview_delete_all_btn = gr.Button("预览全部删除")
                                    cache_delete_btn = gr.Button("删除全部缓存", variant="stop")
                                    cache_compact_btn = gr.Button("手动清除完全一致 Tag", variant="secondary")
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_delete_tags = gr.Textbox(
                                        label="按包含 Tag 删除缓存",
                                        placeholder="例如: comic,text,speech_bubble,english_text",
                                        lines=1,
                                    )
                                    cache_preview_delete_tags_btn = gr.Button("预览删除这些 Tag")
                                    cache_delete_tags_btn = gr.Button("删除包含这些 Tag", variant="stop")
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_delete_range = gr.Textbox(
                                        label="按序号/范围删除缓存",
                                        placeholder="例如: 100-140, 150, 166",
                                        lines=1,
                                    )
                                    cache_preview_delete_range_btn = gr.Button("预览删除这些序号")
                                    cache_delete_range_btn = gr.Button("删除这些序号", variant="stop")
                                cache_delete_preview = gr.Textbox(label="删除预览", elem_id="ranbooru_cache_delete_preview", interactive=False, lines=8)
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_similar_threshold = gr.Number(
                                        label="相似去重阈值",
                                        minimum=0.5,
                                        maximum=1.0,
                                        value=0.90,
                                        step=0.01,
                                    )
                                    cache_similar_keep = gr.Number(
                                        label="每组最多保留",
                                        minimum=1,
                                        maximum=20,
                                        value=2,
                                        step=1,
                                        precision=0,
                                    )
                                    cache_compact_similar_btn = gr.Button("清除相似 Tag (>=90%)", variant="secondary")
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_export_format = gr.Dropdown(["json", "csv"], label="导出格式", value="json", elem_id="ranbooru_cache_export_format")
                                    cache_export_btn = gr.Button("导出缓存", elem_id="ranbooru_cache_export_btn")
                                with gr.Row(elem_classes=["ranbooru-form-row"]):
                                    cache_import_path = gr.File(
                                        elem_id="ranbooru_cache_import_path",
                                        label="上传要导入的 JSON / CSV",
                                        file_count="single",
                                        file_types=[".json", ".csv"],
                                        type="filepath",
                                    )
                                    cache_import_append = gr.Checkbox(label="导入时追加", value=True)
                                    cache_import_dedupe = gr.Checkbox(label="导入时按来源去重", value=True)
                                    cache_import_preflight_btn = gr.Button("预检导入")
                                    cache_import_btn = gr.Button("导入缓存", variant="primary")
                                cache_import_export_result = gr.Textbox(label="导入/导出结果", elem_id="ranbooru_cache_import_result", interactive=False, lines=8)
                                cache_prompt_batch_payload = gr.Textbox(label="Prompt Batch JSON", elem_id="ranbooru_prompt_batch_payload", visible=False, lines=1)
                                cache_prompt_batch_import_btn = gr.Button("导入 Collector 批次", elem_id="ranbooru_prompt_batch_import_btn")
                                cache_manage_result = gr.Textbox(label="操作结果", interactive=False, lines=1)


        with InputAccordion(False, label="LoRAnado", elem_id=self.elem_id("lo_enable")) as lora_enabled:
            with gr.Group():
                lora_lock_prev = gr.Checkbox(label="Lock previous LoRAs", value=False)
                lora_folder = gr.Textbox(lines=1, label="LoRAs Subfolder")
                lora_amount = gr.Slider(value=1, label="LoRAs Amount", minimum=1, maximum=10, step=1)
            with gr.Group():
                lora_min = gr.Slider(value=-1.0, label="Min LoRAs Weight", minimum=-1.0, maximum=1, step=0.1)
                lora_max = gr.Slider(value=1.0, label="Max LoRAs Weight", minimum=-1.0, maximum=1.0, step=0.1)
                lora_custom_weights = gr.Textbox(lines=1, label="LoRAs Custom Weights")

        search_refresh_btn.click(
            fn=self.refresh_ser,
            inputs=[],
            outputs=[choose_search_txt]
        )

        remove_refresh_btn.click(
            fn=self.refresh_rem,
            inputs=[],
            outputs=[choose_remove_txt]
        )

        # Event handler for loading credentials when booru changes
        booru.change(
            fn=self.load_gelbooru_credentials,
            inputs=[booru],
            outputs=[api_key, user_id, credentials_status, clear_credentials_btn]
        )

        # Event handler for clearing credentials
        clear_credentials_btn.click(
            fn=self.clear_gelbooru_credentials,
            inputs=[booru],
            outputs=[api_key, user_id, credentials_status, clear_credentials_btn]
        )

        # ─── Tag Cache 事件绑定 ───────────────────────────────────────────
        cache_refresh_status_btn.click(
            fn=self._cache_refresh_status,
            inputs=[],
            outputs=[cache_status_display]
        )

        cache_fetch_btn.click(
            fn=self._cache_batch_fetch,
            inputs=[booru, tags, cache_pages, cache_start_page, fringe_benefits,
                    remove_bad_tags, remove_tags, shuffle_tags,
                    change_dash, limit_tags, max_tags, mature_rating,
                    api_key, user_id, save_credentials,
                    use_remove_txt, choose_remove_txt, cache_append_mode, tag_categories,
                    cache_dedupe, cache_min_score_enabled, cache_min_score,
                    cache_keep_all_tags, cache_keep_any_tags, cache_exclude_tags],
            outputs=[cache_fetch_result, cache_status_display]
        )

        cache_next_btn.click(
            fn=self._cache_get_next,
            inputs=[cache_loop_mode, use_preconverted_cache_prompt],
            outputs=[cache_next_output, cache_status_display, cache_current_record_id]
        )

        cache_send_prompt_studio_btn.click(
            fn=_cache_send_to_prompt_studio,
            inputs=[cache_next_output, cache_current_record_id],
            outputs=[cache_prompt_studio_status],
        )

        cache_process_prompt_studio_btn.click(
            fn=_cache_process_with_prompt_studio,
            inputs=[cache_next_output, cache_current_record_id],
            outputs=[cache_prompt_studio_result, cache_prompt_studio_status],
        )

        cache_lookup_id_btn.click(
            fn=self._cache_fill_by_position,
            inputs=[cache_lookup_id, use_preconverted_cache_prompt],
            outputs=[cache_next_output, cache_status_display, cache_current_record_id]
        )

        cache_lookup_id_set_btn.click(
            fn=self._cache_fill_position_to_tag_prompt,
            inputs=[
                cache_lookup_id,
                tag_prompt_input,
                cache_prompt_write_mode,
                use_preconverted_cache_prompt,
            ],
            outputs=[
                cache_next_output,
                cache_status_display,
                tag_prompt_input,
                cache_current_record_id,
            ]
        )

        cache_jump_position_btn.click(
            fn=self._cache_jump_to_position,
            inputs=[cache_jump_position],
            outputs=[cache_jump_result, cache_status_display]
        )

        cache_jump_take_btn.click(
            fn=self._cache_jump_take,
            inputs=[
                cache_jump_position,
                cache_loop_mode,
                use_preconverted_cache_prompt,
            ],
            outputs=[cache_next_output, cache_status_display, cache_current_record_id]
        )

        cache_preview_next_btn.click(
            fn=self._cache_preview_next,
            inputs=[],
            outputs=[cache_preview_output]
        )

        cache_natural_language_save_settings_btn.click(
            fn=self._save_natural_language_settings,
            inputs=[
                cache_natural_language_preset,
                cache_natural_language_backend,
                cache_natural_language_endpoint_policy,
                cache_natural_language_endpoint,
                cache_natural_language_model,
                cache_natural_language_api_key,
                cache_natural_language_timeout,
                cache_prompt_rag_enabled,
                cache_prompt_rag_top_k,
                cache_prompt_rag_min_percentile,
                cache_prompt_rag_context_chars,
            ],
            outputs=[cache_natural_language_settings_status],
        )
        cache_natural_language_clear_settings_btn.click(
            fn=self._clear_natural_language_settings,
            inputs=[],
            outputs=[
                cache_natural_language_preset,
                cache_natural_language_backend,
                cache_natural_language_endpoint_policy,
                cache_natural_language_endpoint,
                cache_natural_language_model,
                cache_natural_language_api_key,
                cache_natural_language_timeout,
                cache_prompt_rag_enabled,
                cache_prompt_rag_top_k,
                cache_prompt_rag_min_percentile,
                cache_prompt_rag_context_chars,
                cache_natural_language_settings_status,
            ],
        )

        cache_natural_language_preview_btn.click(
            fn=self._cache_preview_natural_conversion,
            inputs=[
                cache_natural_language_positions,
                cache_natural_language_only_missing,
            ],
            outputs=[cache_natural_language_preview],
        )
        natural_conversion_event = cache_natural_language_convert_btn.click(
            fn=self._cache_batch_convert_natural,
            inputs=[
                cache_natural_language_positions,
                cache_natural_language_only_missing,
                cache_natural_language_preset,
                cache_natural_language_backend,
                cache_natural_language_endpoint_policy,
                cache_natural_language_endpoint,
                cache_natural_language_model,
                cache_natural_language_api_key,
                cache_natural_language_timeout,
                cache_prompt_rag_enabled,
                cache_prompt_rag_top_k,
                cache_prompt_rag_min_percentile,
                cache_prompt_rag_context_chars,
                cache_natural_cancel_id,
            ],
            outputs=[
                cache_natural_language_preview,
                cache_natural_language_status,
            ],
        )
        cache_natural_language_cancel_btn.click(
            fn=self._cancel_natural_batch,
            inputs=[cache_natural_cancel_id],
            outputs=[cache_natural_language_preview],
            cancels=[natural_conversion_event],
            queue=False,
        )
        cache_natural_language_clear_btn.click(
            fn=self._cache_clear_natural_conversion,
            inputs=[cache_natural_language_positions],
            outputs=[
                cache_natural_language_preview,
                cache_natural_language_status,
            ],
        )
        cache_natural_language_refresh_btn.click(
            fn=self._natural_language_cache_status,
            inputs=[],
            outputs=[cache_natural_language_status],
        )

        cache_reset_btn.click(
            fn=self._cache_reset_index,
            inputs=[],
            outputs=[cache_manage_result, cache_status_display]
        )

        cache_backup_btn.click(
            fn=self._cache_backup,
            inputs=[],
            outputs=[cache_import_export_result]
        )

        cache_restore_deleted_btn.click(
            fn=self._cache_restore_last_deleted,
            inputs=[cache_import_dedupe],
            outputs=[cache_manage_result, cache_status_display]
        )

        cache_preview_delete_all_btn.click(
            fn=self._cache_preview_delete_all,
            inputs=[],
            outputs=[cache_delete_preview]
        )

        cache_delete_btn.click(
            fn=self._cache_delete,
            inputs=[],
            outputs=[cache_manage_result, cache_status_display]
        )

        cache_compact_btn.click(
            fn=self._cache_compact_duplicates,
            inputs=[],
            outputs=[cache_manage_result, cache_status_display]
        )

        cache_delete_tags_btn.click(
            fn=self._cache_delete_by_tags,
            inputs=[cache_delete_tags],
            outputs=[cache_manage_result, cache_status_display]
        )

        cache_preview_delete_tags_btn.click(
            fn=self._cache_preview_delete_by_tags,
            inputs=[cache_delete_tags],
            outputs=[cache_delete_preview]
        )

        cache_preview_delete_range_btn.click(
            fn=self._cache_preview_delete_by_position_range,
            inputs=[cache_delete_range],
            outputs=[cache_delete_preview]
        )

        cache_delete_range_btn.click(
            fn=self._cache_delete_by_position_range,
            inputs=[cache_delete_range],
            outputs=[cache_manage_result, cache_status_display]
        )

        cache_compact_similar_btn.click(
            fn=self._cache_compact_similar,
            inputs=[cache_similar_threshold, cache_similar_keep],
            outputs=[cache_manage_result, cache_status_display]
        )

        cache_export_btn.click(
            fn=self._cache_export,
            inputs=[cache_export_format],
            outputs=[cache_import_export_result]
        )

        cache_import_preflight_btn.click(
            fn=self._cache_import_preflight,
            inputs=[cache_import_path, cache_import_append, cache_import_dedupe],
            outputs=[cache_import_export_result]
        )

        cache_import_btn.click(
            fn=self._cache_import,
            inputs=[cache_import_path, cache_import_append, cache_import_dedupe],
            outputs=[cache_import_export_result, cache_status_display]
        )

        cache_prompt_batch_import_btn.click(
            fn=self._cache_import_payload,
            inputs=[cache_prompt_batch_payload, cache_import_append, cache_import_dedupe],
            outputs=[cache_import_export_result, cache_status_display],
        )

        cache_search_btn.click(
            fn=self._cache_search,
            inputs=[cache_search_keyword],
            outputs=[cache_search_results]
        )

        cache_fill_btn.click(
            fn=self._cache_fill_by_id,
            inputs=[cache_select_id, use_preconverted_cache_prompt],
            outputs=[cache_next_output, cache_manage_result, cache_current_record_id]
        )

        cache_delete_id_btn.click(
            fn=self._cache_delete_by_id,
            inputs=[cache_select_id],
            outputs=[cache_manage_result, cache_status_display]
        )

        cache_preview_delete_id_btn.click(
            fn=self._cache_preview_delete_by_id,
            inputs=[cache_select_id],
            outputs=[cache_delete_preview]
        )

        target_prompt_box = self.prompt_area[1 if is_img2img else 0]
        if target_prompt_box is None:
            try:
                from modules.ui import txt2img_paste_fields, img2img_paste_fields
                if is_img2img and img2img_paste_fields and 'prompt' in img2img_paste_fields:
                    target_prompt_box = img2img_paste_fields['prompt']
                elif not is_img2img and txt2img_paste_fields and 'prompt' in txt2img_paste_fields:
                    target_prompt_box = txt2img_paste_fields['prompt']
            except Exception:
                target_prompt_box = None

        if target_prompt_box is not None:
            generate_prompt_btn.click(
                fn=self.generate_and_set_prompt,
                inputs=[booru, max_pages, post_id, tags, remove_bad_tags, remove_tags, change_background, change_color, shuffle_tags, change_dash, mix_prompt, mix_amount, use_search_txt, choose_search_txt, use_remove_txt, choose_remove_txt, fringe_benefits, use_cache, api_key, user_id, save_credentials, mature_rating, sorting_order, limit_tags, max_tags, tag_prompt_input, target_prompt_box, tag_categories, cache_prompt_write_mode],
                outputs=[prompt_output, target_prompt_box]
            )

            # Tag Cache: 取出并设置到提示词
            cache_next_set_btn.click(
                fn=self._cache_get_next_and_set,
                inputs=[
                    cache_loop_mode,
                    tag_prompt_input,
                    target_prompt_box,
                    cache_prompt_write_mode,
                    use_preconverted_cache_prompt,
                ],
                outputs=[
                    cache_next_output,
                    cache_status_display,
                    target_prompt_box,
                    cache_current_record_id,
                ]
            )

            cache_jump_take_set_btn.click(
                fn=self._cache_jump_take_and_set,
                inputs=[
                    cache_jump_position,
                    cache_loop_mode,
                    tag_prompt_input,
                    target_prompt_box,
                    cache_prompt_write_mode,
                    use_preconverted_cache_prompt,
                ],
                outputs=[
                    cache_next_output,
                    cache_status_display,
                    target_prompt_box,
                    cache_current_record_id,
                ]
            )
        else:
            generate_prompt_btn.click(
                fn=self.generate_prompts_only,
                inputs=[booru, max_pages, post_id, tags, remove_bad_tags, remove_tags, change_background, change_color, shuffle_tags, change_dash, mix_prompt, mix_amount, use_search_txt, choose_search_txt, use_remove_txt, choose_remove_txt, fringe_benefits, use_cache, api_key, user_id, save_credentials, mature_rating, sorting_order, limit_tags, max_tags, tag_categories],
                outputs=[prompt_output]
            )

            # Tag Cache: 取出到输出框（无法设置到主提示词框）
            cache_next_set_btn.click(
                fn=self._cache_get_next,
                inputs=[cache_loop_mode, use_preconverted_cache_prompt],
                outputs=[cache_next_output, cache_status_display, cache_current_record_id]
            )

            cache_jump_take_set_btn.click(
                fn=self._cache_jump_take,
                inputs=[
                    cache_jump_position,
                    cache_loop_mode,
                    use_preconverted_cache_prompt,
                ],
                outputs=[cache_next_output, cache_status_display, cache_current_record_id]
            )

        return [enabled, tags, booru, remove_bad_tags, max_pages, change_dash, same_prompt, fringe_benefits, remove_tags, use_img2img, denoising, use_last_img, change_background, change_color, shuffle_tags, post_id, mix_prompt, mix_amount, chaos_mode, negative_mode, chaos_amount, limit_tags, max_tags, sorting_order, mature_rating, lora_folder, lora_amount, lora_min, lora_max, lora_enabled, lora_custom_weights, lora_lock_prev, use_ip, use_search_txt, use_remove_txt, choose_search_txt, choose_remove_txt, crop_center, use_deepbooru, type_deepbooru, use_same_seed, use_cache, api_key, user_id, save_credentials, use_local_cache_gen, use_local_cache_loop, tag_categories, cache_prompt_write_mode, use_preconverted_cache_prompt]

    def check_orientation(self, img):
        """Check if image is portrait, landscape or square"""
        x, y = img.size
        if x / y > 1.2:
            return [768, 512]
        elif y / x > 1.2:
            return [512, 768]
        else:
            return [768, 768]

    def loranado(self, lora_enabled, lora_folder, lora_amount, lora_min, lora_max, lora_custom_weights, p, lora_lock_prev):
        lora_prompt = ''
        if lora_enabled:
            if lora_lock_prev:
                lora_prompt = self.previous_loras
            else:
                try:
                    loras = os.listdir(f'{lora_folder}')
                except Exception as error:
                    logger.error("Could not list LoRA folder %s: %s", lora_folder, error)
                    return p
                # get only .safetensors files
                loras = [lora.replace('.safetensors', '') for lora in loras if lora.endswith('.safetensors')]
                if not loras:
                    logger.warning("No .safetensors LoRAs found in %s; skipping LoRA injection", lora_folder)
                    return p
                custom_weights = []
                if lora_custom_weights != '':
                    custom_weights = [value.strip() for value in lora_custom_weights.split(',')]
                for lora_index in range(0, lora_amount):
                    lora_weight = 0
                    if lora_index < len(custom_weights):
                        try:
                            lora_weight = float(custom_weights[lora_index])
                        except Exception:
                            lora_weight = 0
                    if float(lora_min) == 0 and float(lora_max) == 0:
                        lora_weight = 1.0
                    while lora_weight == 0:
                        lora_weight = round(random.uniform(lora_min, lora_max), 1)
                    lora_prompt += f'<lora:{random.choice(loras)}:{lora_weight}>'
                    self.previous_loras = lora_prompt
        if lora_prompt:
            if isinstance(p.prompt, list):
                for num, pr in enumerate(p.prompt):
                    p.prompt[num] = f'{lora_prompt} {pr}'
            else:
                p.prompt = f'{lora_prompt} {p.prompt}'
        return p

    def before_process(self, p, enabled, tags, booru, remove_bad_tags, max_pages, change_dash, same_prompt, fringe_benefits, remove_tags, use_img2img, denoising, use_last_img, change_background, change_color, shuffle_tags, post_id, mix_prompt, mix_amount, chaos_mode, negative_mode, chaos_amount, limit_tags, max_tags, sorting_order, mature_rating, lora_folder, lora_amount, lora_min, lora_max, lora_enabled, lora_custom_weights, lora_lock_prev, use_ip, use_search_txt, use_remove_txt, choose_search_txt, choose_remove_txt, crop_center, use_deepbooru, type_deepbooru, use_same_seed, use_cache, api_key, user_id, save_credentials, use_local_cache_gen, use_local_cache_loop, tag_categories, *args):
        max_pages = _normalize_max_pages(max_pages)
        cache_prompt_write_mode = args[0] if args else "追加到后面"
        use_preconverted_cache_prompt = bool(args[1]) if len(args) > 1 else True
        job_id = _processing_job_id(p)
        if self._active_job_id != job_id and not _is_hires_second_pass(p):
            self._active_job_id = job_id
            self.last_img = []
            self.real_steps = 0
            self.original_prompt = p.prompt if isinstance(p.prompt, list) else str(p.prompt or '')
        if use_cache and not HAS_REQUESTS_CACHE:
            logger.warning("requests-cache is not installed; running without cache")

        if enabled:
            if use_local_cache_gen:
                # 任务ID（A1111 通常有 job_timestamp）
                # 1) 先拦截 Hires.fix 第二段
                if _is_hires_second_pass(p):
                    logger.debug("Hires.fix second pass; skipping local cache without advancing cursor")
                    return
                # 2) 再做同任务防重（保险）
                if job_id in self._local_cache_applied_jobs:
                    logger.debug("Job %s already received cache prompts; skipping duplicate injection", job_id)
                    return
                # Keep the current job in the dedupe set while bounding memory.
                # Clearing the whole set here used to remove the just-added job
                # at the 201st entry, allowing a second hook call to advance the
                # shared cache cursor twice.
                if len(self._local_cache_applied_jobs) >= 200:
                    oldest_job_id = next(iter(self._local_cache_applied_jobs))
                    self._local_cache_applied_jobs.pop(oldest_job_id, None)
                self._local_cache_applied_jobs[job_id] = None
                logger.info("Local cache mode enabled; injecting cached prompts")
                # 下面保持你原来的取缓存逻辑不变...

                # 计算本次批量生成的总数量 (Batch count * Batch size)
                total_images = p.batch_size * p.n_iter
                cache_entries, idx, total = tag_cache_manager.get_next_tags_batch_from_pool(
                    total_images,
                    loop=use_local_cache_loop,
                    prefer_natural=use_preconverted_cache_prompt,
                    include_prompt_metadata=True,
                )
                cache_prompts = [entry["prompt"] for entry in cache_entries]
                natural_prompt_flags = [bool(entry["is_natural"]) for entry in cache_entries]
                if len(cache_prompts) < total_images:
                    logger.warning(
                        "Cache exhausted at index %s/%s; missing %s prompts",
                        idx,
                        total,
                        total_images - len(cache_prompts),
                    )
                    cache_prompts.extend([""] * (total_images - len(cache_prompts)))
                    natural_prompt_flags.extend(
                        [False] * (total_images - len(natural_prompt_flags))
                    )
                else:
                    logger.info(
                        "Atomically read %s cache prompts at index %s/%s",
                        len(cache_prompts),
                        idx,
                        total,
                    )
                if change_dash:
                    cache_prompts = [
                        prompt if natural_prompt_flags[index] else prompt.replace("_", " ")
                        for index, prompt in enumerate(cache_prompts)
                    ]
                # 将缓存的 Tag 追加到用户输入的 Prompt 后面
                # 如果 p.prompt 是字符串（单张），转为列表处理；如果是列表（多张），则一一对应
                p = self._apply_local_cache_prompt_pipeline(
                    p,
                    cache_prompts,
                    natural_prompt_flags,
                    cache_prompt_write_mode,
                    same_prompt,
                    shuffle_tags,
                    remove_bad_tags,
                    remove_tags,
                    change_background,
                    change_color,
                    chaos_mode,
                    negative_mode,
                    chaos_amount,
                    limit_tags,
                    max_tags,
                    use_same_seed,
                )
                # 处理 Lora (保留原有的 Lora 逻辑)
                if lora_enabled:
                    p = self.loranado(lora_enabled, lora_folder, lora_amount, lora_min, lora_max, lora_custom_weights, p, lora_lock_prev)
                    _sync_prompt_lists(p)
                if use_img2img or use_ip or use_deepbooru:
                    logger.warning(
                        "Local cache mode has no guaranteed source image; skipped Img2Img, "
                        "ControlNet image injection, and DeepBooru"
                    )

                # 直接结束 before_process，跳过后续所有联网代码
                return
            gelbooru_api_key = None
            gelbooru_user_id = None
            rule34_api_key = None
            rule34_user_id = None
            if booru == 'gelbooru':
                # Use provided credentials or load from saved credentials
                if api_key.strip() and user_id.strip():
                    gelbooru_api_key = api_key.strip()
                    gelbooru_user_id = user_id.strip()

                    # Save credentials if checkbox is checked
                    if save_credentials:
                        credentials_manager.save_booru_credentials('gelbooru', gelbooru_api_key, gelbooru_user_id)
                else:
                    # Try to load saved credentials
                    saved_credentials = credentials_manager.get_booru_credentials('gelbooru')
                    gelbooru_api_key = saved_credentials.get('api_key', '')
                    gelbooru_user_id = saved_credentials.get('user_id', '')
            if booru == 'rule34':
                if api_key.strip() and user_id.strip():
                    rule34_api_key = api_key.strip()
                    rule34_user_id = user_id.strip()
                    if save_credentials:
                        credentials_manager.save_booru_credentials('rule34', rule34_api_key, rule34_user_id)
                else:
                    saved_credentials = credentials_manager.get_booru_credentials('rule34')
                    rule34_api_key = saved_credentials.get('api_key', '')
                    rule34_user_id = saved_credentials.get('user_id', '')

            self.original_prompt = p.prompt if isinstance(p.prompt, list) else str(p.prompt or '')
            # Check if compatible
            try:
                check_exception(booru, {'tags': tags, 'post_id': post_id})
            except Exception as error:
                logger.error("%s; skipping prompt injection", error)
                return p

            # Manage Bad Tags — use global DEFAULT_BAD_TAGS
            bad_tags = []
            if remove_bad_tags:
                bad_tags = list(DEFAULT_BAD_TAGS)

            if ',' in remove_tags:
                bad_tags.extend(remove_tags.split(','))
            else:
                if remove_tags:
                    bad_tags.append(remove_tags)

            if use_remove_txt:
                bad_tags.extend(_safe_read_csv_file(user_remove_dir, choose_remove_txt))

            # Manage Backgrounds
            background_options = {
                'Add Background': ('detailed_background,' + random.choice(["outdoors", "indoors"]), COLORED_BG),
                'Remove Background': ('plain_background,simple_background,' + random.choice(COLORED_BG), ADD_BG),
                'Remove All': ('', COLORED_BG + ADD_BG)
            }

            if change_background in background_options:
                prompt_addition, tags_to_remove = background_options[change_background]
                bad_tags.extend(tags_to_remove)
                if isinstance(p.prompt, list):
                    p.prompt = [_append_tags(prompt, prompt_addition) for prompt in p.prompt]
                else:
                    p.prompt = _append_tags(p.prompt, prompt_addition)

            # Manage Colors
            color_options = {
                'Colored': BW_BG,
                'Limited Palette': '(limited_palette:1.3)',
                'Monochrome': ','.join(BW_BG)
            }

            if change_color in color_options:
                color_option = color_options[change_color]
                if isinstance(color_option, list):
                    bad_tags.extend(color_option)
                else:
                    if isinstance(p.prompt, list):
                        p.prompt = [_append_tags(prompt, color_option) for prompt in p.prompt]
                    else:
                        p.prompt = _append_tags(p.prompt, color_option)

            if use_search_txt:
                search_tags = _safe_read_text_file(user_search_dir, choose_search_txt)
                search_tags_r = search_tags.replace(" ", "")
                split_tags = search_tags_r.splitlines()
                filtered_tags = [line for line in split_tags if line.strip()]
                if filtered_tags:
                    selected_tags = random.choice(filtered_tags)
                    tags = f'{tags},{selected_tags}' if tags else selected_tags
                else:
                    logger.warning("No tags found in search file; skipping")

            # Getting Data
            random_post = {'preview_url': ''}
            prompts = []
            last_img = []
            preview_urls = []
            logger.info("Using %s", booru)
            credential = CredentialInput(
                service=booru,
                api_key=(gelbooru_api_key if booru == 'gelbooru' else rule34_api_key) or '',
                user_id=(gelbooru_user_id if booru == 'gelbooru' else rule34_user_id) or '',
            )
            request = BooruRequestConfig(
                service=booru,
                tags=str(tags or ''),
                max_pages=max_pages,
                post_id=str(post_id or ''),
                mature_rating=mature_rating,
                sorting_order=sorting_order,
                tag_categories=tuple(tag_categories or ()),
            )
            api_url = _create_booru_api(
                booru,
                fringe_benefits,
                gelbooru_api_key,
                gelbooru_user_id,
                rule34_api_key,
                rule34_user_id,
            )
            api_url.configure_http_cache(use_cache)
            try:
                result = generate_online_prompt(
                    request,
                    PromptTransformConfig(
                        remove_bad_tags=True,
                        custom_remove=tuple(bad_tags),
                        shuffle_tags=shuffle_tags,
                        mix_amount=mix_amount if mix_prompt else 0,
                    ),
                    credential,
                    lambda pipeline_request, _credential: _adapt_pipeline_client(
                        api_url,
                        pipeline_request,
                        fringe_benefits,
                    ),
                    rng=random,
                )
            finally:
                api_url.close()
            logger.info("Ranbooru fetch status=%s evidence=%s", result.fetch_status, result.fetch_evidence)
            if result.error:
                logger.error("%s", _format_ranbooru_error(booru, result.error))
                return p
            if result.empty:
                logger.warning("No posts found; skipping Ranbooru prompt injection")
                return p
            posts = [
                {
                    **post,
                    'tags': ' '.join(post['tags']),
                    'source': post['source_url'],
                }
                for post in result.posts
            ]
            if post_id:
                logger.debug("Using post ID %s", post_id)
                random_numbers = [0 for _ in range(0, p.batch_size * p.n_iter)]
            else:
                random_numbers = self.random_number(sorting_order, p.batch_size * p.n_iter, len(posts))
            for random_number in random_numbers:
                post_index = random_numbers[0] if same_prompt else random_number
                if post_index >= len(posts):
                    logger.warning("Selected post index is out of range; skipping this prompt")
                    continue
                random_post = posts[post_index]
                if not same_prompt and mix_prompt:
                    temp_tags = []
                    mix_max_tags = 0
                    for _ in range(0, mix_amount):
                        random_mix_number = 0 if post_id else self.random_number(sorting_order, 1, len(posts))[0]
                        mix_tags = _get_post_tags(posts[random_mix_number]).split(' ')
                        temp_tags.extend(mix_tags)
                        mix_max_tags = max(mix_max_tags, len(mix_tags))
                    temp_tags = list(set(temp_tags))
                    mix_max_tags = min(max(len(temp_tags), 20), mix_max_tags)
                    if temp_tags and mix_max_tags > 0:
                        random_post['tags'] = ' '.join(random.sample(temp_tags, min(len(temp_tags), mix_max_tags)))
                raw_tags = _get_post_tags(random_post)
                if not raw_tags:
                    continue
                temp_tags = random.sample(raw_tags.split(' '), len(raw_tags.split(' '))) if shuffle_tags else raw_tags.split(' ')
                prompts.append(' '.join(temp_tags))
                preview_urls.append(_get_post_file_url(random_post))
                # Debug picture
                if DEBUG:
                    logger.debug("Selected post metadata: %s", redact_sensitive(random_post))
            # Get Images
            if use_img2img or use_deepbooru:
                image_urls = [_get_post_file_url(random_post)] if use_last_img else preview_urls
                image_api_url = _create_booru_api(
                    booru,
                    fringe_benefits,
                    gelbooru_api_key,
                    gelbooru_user_id,
                    rule34_api_key,
                    rule34_user_id,
                )
                image_api_url.configure_http_cache(use_cache)
                try:
                    for img in image_urls:
                        image = _fetch_image(
                            image_api_url.fetch_with_retry,
                            img,
                            headers=image_api_url.headers,
                        )
                        if image is not None:
                            last_img.append(image)
                finally:
                    image_api_url.close()
                if not last_img:
                    log_event(logger, "image_failure", "Could not fetch any images; disabling img2img/DeepBooru for this run")
                    use_img2img = False
                    use_ip = False
                    use_deepbooru = False
            new_prompts = []
            # Cleaning Tags
            for prompt in prompts:
                prompt_tags = [tag for tag in html.unescape(prompt).split(' ') if tag.strip() not in bad_tags]
                for bad_tag in bad_tags:
                    if '*' in bad_tag:
                        prompt_tags = [tag for tag in prompt_tags if bad_tag.replace('*', '') not in tag]
                new_prompt = ','.join(prompt_tags)
                if change_dash:
                    new_prompt = new_prompt.replace('_', ' ')
                new_prompts.append(new_prompt)
            prompts = new_prompts
            if not prompts:
                logger.warning("No usable tags found after filtering; skipping prompt injection")
                return p
            if len(prompts) == 1:
                logger.debug("Processing single prompt")
                if isinstance(p.prompt, list):
                    p.prompt = [_append_tags(prompt, prompts[-1]) for prompt in p.prompt]
                else:
                    p.prompt = _append_tags(p.prompt, prompts[-1])
                if chaos_mode in ['Chaos', 'Less Chaos']:
                    base_prompts = p.prompt if isinstance(p.prompt, list) else [p.prompt]
                    base_negative_prompts = _repeat_to_length(p.negative_prompt, len(base_prompts))
                    new_prompts = []
                    new_negative_prompts = []
                    for prompt, base_negative_prompt in zip(base_prompts, base_negative_prompts):
                        negative_prompt = '' if chaos_mode == 'Less Chaos' else base_negative_prompt
                        tmp_prompt, generated_negative_prompt = generate_chaos(prompt, negative_prompt, chaos_amount)
                        new_prompts.append(tmp_prompt)
                        new_negative_prompts.append(
                            _append_tags(base_negative_prompt, generated_negative_prompt)
                            if chaos_mode == 'Less Chaos'
                            else generated_negative_prompt
                        )
                    p.prompt = new_prompts if len(new_prompts) > 1 else new_prompts[0]
                    p.negative_prompt = (
                        new_negative_prompts if len(new_negative_prompts) > 1 else new_negative_prompts[0]
                    )
            else:
                logger.debug("Processing multiple prompts")
                base_prompts = _repeat_to_length(p.prompt, len(prompts))
                base_negative_prompts = _repeat_to_length(p.negative_prompt, len(prompts))
                negative_prompts = []
                new_prompts = []
                if chaos_mode == 'Chaos':
                    for prompt, base_negative_prompt in zip(prompts, base_negative_prompts):
                        tmp_prompt, negative_prompt = generate_chaos(prompt, base_negative_prompt, chaos_amount)
                        new_prompts.append(tmp_prompt)
                        negative_prompts.append(negative_prompt)
                    prompts = new_prompts
                    p.negative_prompt = negative_prompts
                elif chaos_mode == 'Less Chaos':
                    for prompt in prompts:
                        tmp_prompt, negative_prompt = generate_chaos(prompt, '', chaos_amount)
                        new_prompts.append(tmp_prompt)
                        negative_prompts.append(negative_prompt)
                    prompts = new_prompts
                    p.negative_prompt = [
                        _append_tags(base_negative_prompt, negative_prompt)
                        for base_negative_prompt, negative_prompt in zip(base_negative_prompts, negative_prompts)
                    ]
                else:
                    p.negative_prompt = base_negative_prompts
                p.prompt = [
                    _append_tags(base_prompt, prompt)
                    for base_prompt, prompt in zip(base_prompts, prompts)
                ]
                if use_img2img and last_img:
                    if len(last_img) < p.batch_size * p.n_iter:
                        last_img = [last_img[0] for _ in range(0, p.batch_size * p.n_iter)]
            if negative_mode == 'Negative':
                # remove tags from p.prompt using tags from the original prompt
                original_prompts = (
                    self.original_prompt if isinstance(self.original_prompt, list) else [self.original_prompt]
                )
                if isinstance(p.prompt, list):
                    new_positive_prompts = []
                    new_negative_prompts = []
                    negative_prompts = _repeat_to_length(p.negative_prompt, len(p.prompt))
                    for pr, npp, original_prompt in zip(
                        p.prompt,
                        negative_prompts,
                        _repeat_to_length(original_prompts, len(p.prompt)),
                    ):
                        orig_list = str(original_prompt or '').split(',')
                        clean_prompt = pr.split(',')
                        clean_prompt = [tag for tag in clean_prompt if tag not in orig_list]
                        clean_prompt = ','.join(clean_prompt)
                        new_positive_prompts.append(original_prompt)
                        new_negative_prompts.append(_append_tags(npp, clean_prompt))
                    p.prompt = new_positive_prompts
                    p.negative_prompt = new_negative_prompts
                else:
                    orig_list = str(original_prompts[0] if original_prompts else '').split(',')
                    clean_prompt = p.prompt.split(',')
                    clean_prompt = [tag for tag in clean_prompt if tag not in orig_list]
                    clean_prompt = ','.join(clean_prompt)
                    p.negative_prompt = _append_tags(p.negative_prompt, clean_prompt)
                    p.prompt = original_prompts[0] if original_prompts else ''
            if negative_mode == 'Negative' or chaos_mode in ['Chaos', 'Less Chaos']:
                # NEGATIVE PROMPT FIX
                if isinstance(p.negative_prompt, str):
                    p.negative_prompt = [p.negative_prompt for _ in range(0, p.batch_size * p.n_iter)]
                neg_prompt_tokens = []
                for pr in p.negative_prompt:
                    neg_prompt_tokens.append(get_prompt_lengths(pr)[1])
                if len(set(neg_prompt_tokens)) != 1:
                    logger.debug("Padding negative prompts")
                    max_tokens = max(neg_prompt_tokens)
                    for num, neg in enumerate(neg_prompt_tokens):
                        while neg < max_tokens:
                            choices = [tag for tag in str(p.negative_prompt[num] or '').split(',') if tag]
                            if not choices:
                                break
                            p.negative_prompt[num] = _append_tags(
                                p.negative_prompt[num],
                                random.choice(choices),
                            )
                            # p.negative_prompt[num] += '_'
                            neg = get_prompt_lengths(p.negative_prompt[num])[1]

            if limit_tags < 1:
                if isinstance(p.prompt, list):
                    p.prompt = [limit_prompt_tags(pr, limit_tags, 'Limit') for pr in p.prompt]
                else:
                    p.prompt = limit_prompt_tags(p.prompt, limit_tags, 'Limit')

            if max_tags > 0:
                if isinstance(p.prompt, list):
                    p.prompt = [limit_prompt_tags(pr, max_tags, 'Max') for pr in p.prompt]
                else:
                    p.prompt = limit_prompt_tags(p.prompt, max_tags, 'Max')

            if use_same_seed:
                p.seed = random.randint(0, 2 ** 32 - 1) if p.seed == -1 else p.seed
                p.seed = [p.seed] * p.batch_size

            # LORANADO
            p = self.loranado(lora_enabled, lora_folder, lora_amount, lora_min, lora_max, lora_custom_weights, p, lora_lock_prev)
            if use_deepbooru and not use_img2img and last_img:
                self.last_img = last_img
                try:
                    tagged_prompts = self.use_autotagger('deepbooru')
                    if isinstance(p.prompt, list):
                        tagged_prompts = _repeat_to_length(tagged_prompts, len(p.prompt))
                        p.prompt = [modify_prompt(pr, tagged_prompts[num], type_deepbooru) for num, pr in enumerate(p.prompt)]
                        p.prompt = [remove_repeated_tags(pr) for pr in p.prompt]
                    else:
                        tagged_prompt = tagged_prompts[0] if isinstance(tagged_prompts, list) and tagged_prompts else tagged_prompts
                        p.prompt = modify_prompt(p.prompt, tagged_prompt, type_deepbooru)
                        p.prompt = remove_repeated_tags(p.prompt)
                except Exception as error:
                    logger.error("DeepBooru failed: %s", error)
            _sync_prompt_lists(p)

            if use_img2img and last_img:
                if not use_ip:
                    self.real_steps = p.steps
                    p.steps = 1
                    self.last_img = last_img
                if use_ip:
                    _send_to_controlnet_legacy(p, last_img[0], denoising)

        elif lora_enabled:
            p = self.loranado(lora_enabled, lora_folder, lora_amount, lora_min, lora_max, lora_custom_weights, p, lora_lock_prev)
            _sync_prompt_lists(p)

    def postprocess(self, p, processed, enabled, tags, booru, remove_bad_tags, max_pages, change_dash, same_prompt, fringe_benefits, remove_tags, use_img2img, denoising, use_last_img, change_background, change_color, shuffle_tags, post_id, mix_prompt, mix_amount, chaos_mode, negative_mode, chaos_amount, limit_tags, max_tags, sorting_order, mature_rating, lora_folder, lora_amount, lora_min, lora_max, lora_enabled, lora_custom_weights, lora_lock_prev, use_ip, use_search_txt, use_remove_txt, choose_search_txt, choose_remove_txt, crop_center, use_deepbooru, type_deepbooru, use_same_seed, use_cache, api_key, user_id, save_credentials, use_local_cache_gen, use_local_cache_loop, tag_categories, *args):
        if use_img2img and not use_ip and enabled:
            logger.info("Using source images")
            if not hasattr(self, 'last_img') or not self.last_img:
                logger.warning("No source images available; skipping postprocess img2img")
                return
            if crop_center:
                width, height = p.width, p.height
                self.last_img = [resize_image(img, width, height, cropping=True) for img in self.last_img]
            else:
                width, height = self.check_orientation(self.last_img[0])
            final_prompts = getattr(p, 'all_prompts', None) or p.prompt
            if use_deepbooru:
                try:
                    tagged_prompts = self.use_autotagger('deepbooru')
                    if isinstance(final_prompts, list):
                        tagged_prompts = _repeat_to_length(tagged_prompts, len(final_prompts))
                        final_prompts = [modify_prompt(pr, tagged_prompts[num], type_deepbooru) for num, pr in enumerate(final_prompts)]
                        final_prompts = [remove_repeated_tags(pr) for pr in final_prompts]
                    else:
                        tagged_prompt = tagged_prompts[0] if isinstance(tagged_prompts, list) and tagged_prompts else tagged_prompts
                        final_prompts = modify_prompt(final_prompts, tagged_prompt, type_deepbooru)
                        final_prompts = remove_repeated_tags(final_prompts)
                except Exception as error:
                    logger.error("DeepBooru failed during postprocess: %s", error)
            p = StableDiffusionProcessingImg2Img(
                sd_model=shared.sd_model,
                outpath_samples=shared.opts.outdir_samples or shared.opts.outdir_img2img_samples,
                outpath_grids=shared.opts.outdir_grids or shared.opts.outdir_img2img_grids,
                prompt=final_prompts,
                negative_prompt=p.negative_prompt,
                seed=p.seed,
                sampler_name=p.sampler_name,
                scheduler=p.scheduler,
                batch_size=p.batch_size,
                n_iter=p.n_iter,
                steps=self.real_steps,
                cfg_scale=p.cfg_scale,
                width=width,
                height=height,
                init_images=self.last_img,
                denoising_strength=denoising,
            )
            proc = process_images(p)
            processed.images = proc.images
            processed.infotexts = proc.infotexts
            if use_last_img:
                processed.images.append(self.last_img[0])
            self.last_img = []
            self.real_steps = 0
        elif (
            enabled
            and use_last_img
            and self._active_job_id == _processing_job_id(p)
            and self.last_img
        ):
            processed.images.extend(self.last_img)
            self.last_img = []

    def generate_prompts_only(self, booru, max_pages, post_id, tags, remove_bad_tags, remove_tags, change_background, change_color, shuffle_tags, change_dash, mix_prompt, mix_amount, use_search_txt, choose_search_txt, use_remove_txt, choose_remove_txt, fringe_benefits, use_cache, api_key, user_id, save_credentials, mature_rating, sorting_order, limit_tags, max_tags, tag_categories):
        max_pages = _normalize_max_pages(max_pages)
        if use_cache and not HAS_REQUESTS_CACHE:
            logger.warning("requests-cache is not installed; running without cache")

        supplied = CredentialInput(
            service=booru,
            api_key=str(api_key or '').strip(),
            user_id=str(user_id or '').strip(),
        )
        credential = resolve_credentials(booru, supplied, _pipeline_saved_credentials(booru))
        if save_credentials and credential.api_key and credential.user_id and booru in ('gelbooru', 'rule34'):
            credentials_manager.save_booru_credentials(booru, credential.api_key, credential.user_id)

        custom_remove = [item.strip() for item in str(remove_tags or '').split(',') if item.strip()]
        if use_remove_txt:
            custom_remove.extend(_safe_read_csv_file(user_remove_dir, choose_remove_txt))
        if use_search_txt:
            search_tags = [
                line.strip()
                for line in _safe_read_text_file(user_search_dir, choose_search_txt).splitlines()
                if line.strip()
            ]
            if search_tags:
                selected_tags = random.choice(search_tags).replace(' ', '')
                tags = f'{tags},{selected_tags}' if tags else selected_tags

        background_tags = []
        if change_background == 'Add Background':
            background_tags = ('detailed_background', random.choice(('outdoors', 'indoors')))
        elif change_background == 'Remove Background':
            background_tags = ('plain_background', 'simple_background', random.choice(COLORED_BG))
            custom_remove.extend(ADD_BG)
        elif change_background == 'Remove All':
            custom_remove.extend(COLORED_BG + ADD_BG)
        color_tags = []
        if change_color == 'Colored':
            custom_remove.extend(BW_BG)
        elif change_color == 'Limited Palette':
            color_tags = ('(limited_palette:1.3)',)
        elif change_color == 'Monochrome':
            color_tags = tuple(BW_BG)

        request = BooruRequestConfig(
            service=booru,
            tags=str(tags or ''),
            max_pages=max_pages,
            post_id=str(post_id or ''),
            mature_rating=mature_rating,
            sorting_order=sorting_order,
            tag_categories=tuple(tag_categories or ()),
        )
        transform = PromptTransformConfig(
            remove_bad_tags=remove_bad_tags,
            bad_tags=tuple(DEFAULT_BAD_TAGS),
            custom_remove=tuple(custom_remove),
            change_dash=change_dash,
            shuffle_tags=shuffle_tags,
            limit_ratio=limit_tags if limit_tags < 1 else 1.0,
            max_tags=max_tags,
            background_tags=tuple(background_tags),
            color_tags=tuple(color_tags),
            mix_amount=mix_amount if mix_prompt else 0,
        )
        result = generate_online_prompt(
            request,
            transform,
            credential,
            _pipeline_client_factory(use_cache, fringe_benefits),
            rng=random,
        )
        if result.error:
            return _format_ranbooru_error(booru, result.error)
        if result.empty or not result.prompt:
            return NO_POSTS_MESSAGE
        return result.prompt

    def generate_and_set_prompt(self, booru, max_pages, post_id, tags, remove_bad_tags, remove_tags, change_background, change_color, shuffle_tags, change_dash, mix_prompt, mix_amount, use_search_txt, choose_search_txt, use_remove_txt, choose_remove_txt, fringe_benefits, use_cache, api_key, user_id, save_credentials, mature_rating, sorting_order, limit_tags, max_tags, tag_prompt_text, current_prompt, tag_categories, write_mode):
        final_prompt = self.generate_prompts_only(booru, max_pages, post_id, tags, remove_bad_tags, remove_tags, change_background, change_color, shuffle_tags, change_dash, mix_prompt, mix_amount, use_search_txt, choose_search_txt, use_remove_txt, choose_remove_txt, fringe_benefits, use_cache, api_key, user_id, save_credentials, mature_rating, sorting_order, limit_tags, max_tags, tag_categories)
        if (
            not final_prompt
            or final_prompt.strip() == ''
            or final_prompt == NO_POSTS_MESSAGE
            or final_prompt.startswith('Ranbooru:')
        ):
            return final_prompt, current_prompt
        prompt_payload = Script._combine_prompt(tag_prompt_text, final_prompt)
        combined_prompt = Script._apply_write_mode(current_prompt, prompt_payload, write_mode)
        return prompt_payload, combined_prompt

    def random_number(self, sorting_order, size, count):
        """Generates random numbers based on the sorting_order

        Args:
            sorting_order (str): the sorting order. It can be 'Random', 'High Score' or 'Low Score'
            size (int): the amount of random numbers to generate

        Returns:
            list: the random numbers
        """
        if count <= 0:
            raise Exception("No posts found with those tags. Try lowering the pages or changing the tags.")
        if count > POST_AMOUNT:
            count = POST_AMOUNT
        weights = np.arange(count, 0, -1)
        weights = weights / weights.sum()
        if sorting_order in ('High Score', 'Low Score'):
            if size <= count:
                random_numbers = np.random.choice(np.arange(count), size=size, p=weights, replace=False).tolist()
            else:
                random_numbers = np.random.choice(np.arange(count), size=size, p=weights, replace=True).tolist()
        else:
            if size <= count:
                random_numbers = random.sample(range(count), size)
            else:
                random_numbers = np.random.choice(np.arange(count), size=size, replace=True).tolist()
        return random_numbers

    def use_autotagger(self, model):
        """Use the autotagger to tag the images

        Args:
            model (str): the model to use. Right now only 'deepbooru' is supported

        Returns:
            list: the tagged prompts
        """
        if model == 'deepbooru':
            if not has_deepbooru():
                raise RuntimeError("DeepBooru is not available in this WebUI build.")
            if isinstance(self.original_prompt, str):
                orig_prompt = [self.original_prompt]
            else:
                orig_prompt = self.original_prompt
            deepbooru.model.start()
            try:
                final_prompts = [prompt + ',' + deepbooru.model.tag_multi(img) for img, prompt in zip(self.last_img, orig_prompt)]
            finally:
                deepbooru.model.stop()
            return final_prompts
