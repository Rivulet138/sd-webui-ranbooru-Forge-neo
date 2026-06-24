from io import BytesIO
import html
import random
import re
import requests
import modules.scripts as scripts
from modules.scripts import OnComponent
import gradio as gr
import os
from PIL import Image
import numpy as np
import importlib
import json
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
    if _ranbooru_scripts_dir not in sys.path:
        sys.path.insert(0, _ranbooru_scripts_dir)
    from cache_db import TagCacheManager


tag_cache_manager = TagCacheManager(user_cache_dir)

# Initialize credentials manager
class CredentialsManager:
    def __init__(self, extension_root):
        self.extension_root = extension_root
        self.credentials_dir = os.path.join(extension_root, 'user', 'credentials')
        self.credentials_file = os.path.join(self.credentials_dir, 'credentials.json')
        
        # Create credentials directory if it doesn't exist
        os.makedirs(self.credentials_dir, exist_ok=True)
        
        # Initialize credentials file if it doesn't exist
        if not os.path.exists(self.credentials_file):
            self._save_credentials({})
    
    def _load_credentials(self):
        """Load credentials from the JSON file"""
        try:
            with open(self.credentials_file, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
    
    def _save_credentials(self, credentials):
        """Save credentials to the JSON file"""
        with open(self.credentials_file, 'w') as f:
            json.dump(credentials, f, indent=2)
    
    def save_booru_credentials(self, booru_name, api_key, user_id=None):
        """Save API credentials for a specific booru"""
        credentials = self._load_credentials()
        
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
        credentials = self._load_credentials()
        if booru_name in credentials:
            del credentials[booru_name]
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
COUNT = 100 #Number of images the search returned. Booru classes below were modified to update this value with the latest search result count.
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
        return gr.update(visible=True)
    else:
        return gr.update(visible=False)


def check_exception(booru, parameters):
    post_id = parameters.get('post_id')
    tags = parameters.get('tags')
    if booru == 'konachan' and post_id:
        raise Exception("Konachan does not support post IDs")
    if booru == 'yande.re' and post_id:
        raise Exception("Yande.re does not support post IDs")


class Booru():

    def __init__(self, booru, booru_url):
        self.booru = booru
        self.base_url = booru_url
        self.booru_url = booru_url
        self.headers = {'user-agent': 'my-app/0.0.1'}

    def fetch_with_retry(self, url, max_retries=3, timeout=10, **kwargs):
        """Fetch URL with retry logic for rate limiting and network errors."""
        import time
        safe_url = re.sub(r'((?:api_key|user_id)=)[^&\s]+', r'\1***', str(url))
        for attempt in range(max_retries):
            try:
                response = requests.get(url, timeout=timeout, **kwargs)
                if response.status_code == 429:
                    wait = 2 ** attempt
                    print(f"[{self.booru}] Rate limited (429), waiting {wait}s...")
                    time.sleep(wait)
                    continue
                response.raise_for_status()
                return response
            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    print(f"[{self.booru}] Timeout, retry {attempt + 1}/{max_retries}")
                    time.sleep(2 ** attempt)
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    safe_error = re.sub(r'((?:api_key|user_id)=)[^&\s]+', r'\1***', str(e))
                    print(f"[{self.booru}] Request error: {safe_error}, retry {attempt + 1}/{max_retries}")
                    time.sleep(2 ** attempt)
        raise Exception(f"[{self.booru}] All {max_retries} attempts failed for {safe_url}")

    def get_data(self, add_tags, max_pages=10, id=''):
        pass

    def get_post(self, add_tags, max_pages=10, id=''):
        return self.get_data(add_tags, max_pages, "&id=" + id)


class Gelbooru(Booru):

    def __init__(self, fringe_benefits=True, api_key=None, user_id=None):
        super().__init__('gelbooru', f'https://gelbooru.com/index.php?page=dapi&s=post&q=index&json=1&limit={POST_AMOUNT}')
        self.fringe_benefits = fringe_benefits
        self.api_key = api_key
        self.user_id = user_id

    def get_data(self, add_tags, max_pages=10, id=''):
        global COUNT
        max_pages = _normalize_max_pages(max_pages)
        loop_msg = True
        for _ in range(2):
            local_add_tags = '' if id else add_tags
            url = f"{self.base_url}&pid={random.randint(0, max_pages-1)}{id}{local_add_tags}"
            if self.api_key and self.user_id:
                url += f"&api_key={self.api_key}&user_id={self.user_id}"
            if self.fringe_benefits:
                url += "&fringeBenefits=1"
            self.booru_url = url
            res = self.fetch_with_retry(url, timeout=10)
            try:
                data = res.json()
            except Exception as e:
                print(f"[Gelbooru] JSON parse error: {e}")
                data = []
            if not isinstance(data, list):
                data = []
            COUNT = len(data)
            if COUNT == 0:
                max_pages = 2
                while loop_msg:
                    print(f" Processing {COUNT} results.")
                    loop_msg = False
                continue
            break
        for post in data:
            if isinstance(post, dict) and 'directory' in post and 'image' in post:
                post['file_url'] = f"https://img3.gelbooru.com/images/{post['directory']}/{post['image']}"
        return {'post': data}

    def get_data_page(self, add_tags, page=0, id=''):
        global COUNT
        local_add_tags = '' if id else add_tags
        url = f"{self.base_url}&pid={page}{id}{local_add_tags}"
        if self.api_key and self.user_id:
            url += f"&api_key={self.api_key}&user_id={self.user_id}"
        if self.fringe_benefits:
            url += "&fringeBenefits=1"
        self.booru_url = url
        res = self.fetch_with_retry(url, timeout=10)
        try:
            data = res.json()
        except Exception as e:
            print(f"[Gelbooru] JSON parse error (page): {e}")
            data = []
        if not isinstance(data, list):
            data = []
        COUNT = len(data)
        for post in data:
            if isinstance(post, dict) and 'directory' in post and 'image' in post:
                post['file_url'] = f"https://img3.gelbooru.com/images/{post['directory']}/{post['image']}"
        return {'post': data}

    def get_post(self, add_tags, max_pages=10, id=''):
        return self.get_data(add_tags, max_pages, "&id=" + id)


class e621(Booru):

    def __init__(self):
        super().__init__('e621', f'https://e621.net/posts.json?limit={POST_AMOUNT}')

    def get_data(self, add_tags, max_pages=10, id='', tag_categories=None):
        global COUNT
        max_pages = _normalize_max_pages(max_pages)
        loop_msg = True
        for loop in range(2):
            if id:
                add_tags = ''
            random_page = random.randint(1, max(1, int(max_pages)))
            url = f"{self.base_url}&page={random_page}{add_tags}"
            self.booru_url = url
            res = self.fetch_with_retry(url, headers=self.headers, timeout=10)
            data = res.json()
            posts = data.get('posts', []) if isinstance(data, dict) else []
            for post in posts:
                if isinstance(post, dict):
                    post['tags'] = self._filter_tags_by_category(post, tag_categories)
            COUNT = len(posts)
            if COUNT == 0:
                max_pages = 2
                while loop_msg:
                    print(f" Processing {COUNT} results.")
                    loop_msg = False
                continue
            else:
                print(f"Found enough results")
            break
        return {'post': posts}

    def get_data_page(self, add_tags, page=0, id='', tag_categories=None):
        global COUNT
        if id:
            add_tags = ''
        safe_page = max(1, int(page) + 1)
        url = f"{self.base_url}&page={safe_page}{add_tags}"
        self.booru_url = url
        res = self.fetch_with_retry(url, headers=self.headers, timeout=10)
        data = res.json()
        posts = data.get('posts', []) if isinstance(data, dict) else []
        for post in posts:
            if isinstance(post, dict):
                post['tags'] = self._filter_tags_by_category(post, tag_categories)
        COUNT = len(posts)
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
        global COUNT
        max_pages = _normalize_max_pages(max_pages)
        loop_msg = True # avoid showing same msg twice
        for loop in range(2): # run loop at most twice
            if id:
                add_tags = ''
            url = f"{self.base_url}&pid={random.randint(0, max_pages-1)}{id}{add_tags}"
            self.booru_url = url
            print(re.sub(r'((?:api_key|user_id)=)[^&\s]+', r'\1***', str(url)))
            res = self.fetch_with_retry(url, timeout=10)
            data = res.json()
            COUNT = 0
            for post in data:
                if isinstance(post, dict) and 'directory' in post and 'image' in post:
                    post['file_url'] = f"https://xbooru.com/images/{post['directory']}/{post['image']}"
                    COUNT += 1
            if COUNT <= max_pages*POST_AMOUNT:
                max_pages = COUNT // POST_AMOUNT+1
                # If max_pages is bigger than available pages, loop the function with updated max_pages based on the value of COUNT
                while loop_msg:
                    print(f" Processing {COUNT} results.")
                    loop_msg = False
                    # avoid showing same msg twice
                continue
            else:
                print(f" Processing {max_pages*POST_AMOUNT} out of {COUNT} results.")
            break
        return {'post': data}

    def get_data_page(self, add_tags, page=0, id=''):
        global COUNT
        if id:
            add_tags = ''
        url = f"{self.base_url}&pid={page}{id}{add_tags}"
        self.booru_url = url
        res = self.fetch_with_retry(url, timeout=10)
        data = res.json()
        COUNT = 0
        for post in data:
            if isinstance(post, dict) and 'directory' in post and 'image' in post:
                post['file_url'] = f"https://xbooru.com/images/{post['directory']}/{post['image']}"
                COUNT += 1
        return {'post': data}

    def get_post(self, add_tags, max_pages=10, id=''):
        return self.get_data(add_tags, max_pages, "&id=" + id)


class Rule34(Booru):

    def __init__(self, api_key=None, user_id=None):
        super().__init__('rule34', f'https://api.rule34.xxx/index.php?page=dapi&s=post&q=index&json=1&limit={POST_AMOUNT}')
        self.api_key = api_key
        self.user_id = user_id

    def get_data(self, add_tags, max_pages=10, id=''):
        global COUNT
        max_pages = _normalize_max_pages(max_pages)
        loop_msg = True # avoid showing same msg twice
        for loop in range(2): # run loop at most twice
            if id:
                add_tags = ''
            url = f"{self.base_url}&pid={random.randint(0, max_pages-1)}{id}{add_tags}"
            if self.api_key and self.user_id:
                url += f"&api_key={self.api_key}&user_id={self.user_id}"
            self.booru_url = url
            res = self.fetch_with_retry(url, timeout=10)
            try:
                data = res.json()
            except Exception as e:
                print(f"[Rule34] JSON parse error: {e}")
                data = []
            if not isinstance(data, list):
                data = []
            COUNT = len(data)
            if COUNT == 0:
                max_pages = 2
                # Rule34 does not have a way to know the amount of results available in the search, so we need to run the function again with a fixed amount of pages
                while loop_msg:
                    print(f" Processing {COUNT} results.")
                    loop_msg = False
                    # avoid showing same msg twice
                continue
            else:
                print(f"Found enough results")
            break
        return {'post': data}

    def get_data_page(self, add_tags, page=0, id=''):
        global COUNT
        if id:
            add_tags = ''
        url = f"{self.base_url}&pid={page}{id}{add_tags}"
        if self.api_key and self.user_id:
            url += f"&api_key={self.api_key}&user_id={self.user_id}"
        self.booru_url = url
        res = self.fetch_with_retry(url, timeout=10)
        try:
            data = res.json()
        except Exception as e:
            print(f"[Rule34] JSON parse error (page): {e}")
            data = []
        if not isinstance(data, list):
            data = []
        COUNT = len(data)
        return {'post': data}

    def get_post(self, add_tags, max_pages=10, id=''):
        return self.get_data(add_tags, max_pages, "&id=" + id)


class Safebooru(Booru):

    def __init__(self):
        super().__init__('safebooru', f'https://safebooru.donmai.us/posts.json?limit={POST_AMOUNT}')

    def get_data(self, add_tags, max_pages=10, id='', tag_categories=None):
        global COUNT
        max_pages = _normalize_max_pages(max_pages)
        loop_msg = True
        for loop in range(2):
            if id:
                add_tags = ''
            random_page = random.randint(1, max(1, int(max_pages)))
            url = f"{self.base_url}&page={random_page}{add_tags}"
            self.booru_url = url
            res = self.fetch_with_retry(url, headers=self.headers, timeout=10)
            data = res.json()
            if not isinstance(data, list):
                try:
                    data = data.get('posts', [])
                except AttributeError:
                    data = []
            COUNT = 0
            for post in data:
                if isinstance(post, dict):
                    post['tags'] = self._filter_tags_by_category(post, tag_categories)
                    if not post.get('file_url') and post.get('large_file_url'):
                        post['file_url'] = post.get('large_file_url')
                    COUNT += 1
            if COUNT == 0:
                max_pages = 2
                while loop_msg:
                    print(f" Processing {COUNT} results.")
                    loop_msg = False
                continue
            else:
                print(f"Found enough results")
            break
        return {'post': data}

    def get_data_page(self, add_tags, page=0, id='', tag_categories=None):
        global COUNT
        if id:
            add_tags = ''
        safe_page = max(1, int(page) + 1)
        url = f"{self.base_url}&page={safe_page}{add_tags}"
        self.booru_url = url
        res = self.fetch_with_retry(url, headers=self.headers, timeout=10)
        data = res.json()
        if not isinstance(data, list):
            try:
                data = data.get('posts', [])
            except AttributeError:
                data = []
        COUNT = 0
        for post in data:
            if isinstance(post, dict):
                post['tags'] = self._filter_tags_by_category(post, tag_categories)
                if not post.get('file_url') and post.get('large_file_url'):
                    post['file_url'] = post.get('large_file_url')
                COUNT += 1
        return {'post': data}

    def _filter_tags_by_category(self, post, categories):
        """Filter tags by selected categories."""
        if not categories or not isinstance(categories, list):
            return post.get('tag_string', '')
        parts = []
        for cat in categories:
            field = f'tag_string_{cat}'
            if field in post:
                parts.append(post[field])
        return ' '.join(parts) if parts else post.get('tag_string', '')

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
        global COUNT
        max_pages = _normalize_max_pages(max_pages)
        loop_msg = True # avoid showing same msg twice
        for loop in range(2): # run loop at most twice
            if id:
                add_tags = ''
            url = f"{self.base_url}&page={random.randint(0, max_pages-1)}{id}{add_tags}"
            self.booru_url = url
            res = self.fetch_with_retry(url, timeout=10)
            if res.status_code != 200:
                data = []
            else:
                try:
                    data = res.json()
                except Exception as e:
                    print(f"[Konachan] JSON parse error: {e}")
                    data = []
            COUNT = len(data)
            if COUNT == 0:
                max_pages = 2
                # Konachan does not have a way to know the amount of results available in the search, so we need to run the function again with a fixed amount of pages
                while loop_msg:
                    print(f" Processing {COUNT} results.")
                    loop_msg = False
                    # avoid showing same msg twice
                continue
            else:
                print(f"Found enough results")
            break
        return {'post': data}

    def get_data_page(self, add_tags, page=0, id=''):
        global COUNT
        if id:
            add_tags = ''
        url = f"{self.base_url}&page={page}{id}{add_tags}"
        self.booru_url = url
        res = self.fetch_with_retry(url, timeout=10)
        if res.status_code != 200:
            data = []
        else:
            try:
                data = res.json()
            except Exception as e:
                print(f"[Konachan] JSON parse error (page): {e}")
                data = []
        COUNT = len(data)
        return {'post': data}

    def get_post(self, add_tags, max_pages=10, id=''):
        raise Exception("Konachan does not support post IDs")


class Yandere(Booru):

    def __init__(self):
        super().__init__('yande.re', f'https://yande.re/post.json?api_version=2')

    def get_data(self, add_tags, max_pages=10, id=''):
        global COUNT
        max_pages = _normalize_max_pages(max_pages)
        loop_msg = True # avoid showing same msg twice
        for loop in range(2): # run loop at most twice
            if id:
                add_tags = ''
            page = random.randint(0, max_pages-1)
            extras = '&filter=1&include_tags=1&include_votes=1&include_pools=1'
            url = f"{self.base_url}&limit={POST_AMOUNT}&page={page}{id}{add_tags}{extras}"
            self.booru_url = url
            res = self.fetch_with_retry(url, timeout=10)
            posts = []
            if res.status_code == 200:
                try:
                    data = res.json()
                    if isinstance(data, dict):
                        posts = data.get('posts', [])
                    elif isinstance(data, list):
                        posts = data
                except Exception as e:
                    print(f"[Yandere] JSON parse error: {e}")
                    posts = []
            COUNT = len(posts)
            if COUNT == 0:
                max_pages = 2
                # Yandere does not have a way to know the amount of results available in the search, so we need to run the function again with a fixed amount of pages
                while loop_msg:
                    print(f" Processing {COUNT} results.")
                    loop_msg = False
                    # avoid showing same msg twice
                continue
            else:
                print(f"Found enough results")
            break
        return {'post': posts}

    def get_data_page(self, add_tags, page=0, id=''):
        global COUNT
        if id:
            add_tags = ''
        extras = '&filter=1&include_tags=1&include_votes=1&include_pools=1'
        url = f"{self.base_url}&limit={POST_AMOUNT}&page={page}{id}{add_tags}{extras}"
        self.booru_url = url
        res = self.fetch_with_retry(url, timeout=10)
        posts = []
        if res.status_code == 200:
            try:
                data = res.json()
                if isinstance(data, dict):
                    posts = data.get('posts', [])
                elif isinstance(data, list):
                    posts = data
            except Exception:
                posts = []
        COUNT = len(posts)
        return {'post': posts}

    def get_post(self, add_tags, max_pages=10, id=''):
        raise Exception("Yande.re does not support post IDs")


class AIBooru(Booru):

    def __init__(self):
        super().__init__('AIBooru', f'https://aibooru.online/posts.json?limit={POST_AMOUNT}')

    def get_data(self, add_tags, max_pages=10, id='', tag_categories=None):
        global COUNT
        max_pages = _normalize_max_pages(max_pages)
        loop_msg = True
        for loop in range(2):
            if id:
                add_tags = ''
            url = f"{self.base_url}&page={random.randint(0, max_pages-1)}{id}{add_tags}"
            self.booru_url = url
            res = self.fetch_with_retry(url)
            data = res.json()
            if not isinstance(data, list):
                data = data.get('posts', []) if isinstance(data, dict) else []
            for post in data:
                if isinstance(post, dict):
                    post['tags'] = self._filter_tags_by_category(post, tag_categories)
            COUNT = len(data)
            if COUNT == 0:
                max_pages = 2
                while loop_msg:
                    print(f" Processing {COUNT} results.")
                    loop_msg = False
                continue
            else:
                print(f"Found enough results")
            break
        return {'post': data}

    def get_data_page(self, add_tags, page=0, id='', tag_categories=None):
        global COUNT
        if id:
            add_tags = ''
        url = f"{self.base_url}&page={page}{id}{add_tags}"
        self.booru_url = url
        res = self.fetch_with_retry(url)
        data = res.json()
        if not isinstance(data, list):
            data = data.get('posts', []) if isinstance(data, dict) else []
        for post in data:
            if isinstance(post, dict):
                post['tags'] = self._filter_tags_by_category(post, tag_categories)
        COUNT = len(data)
        return {'post': data}

    def _filter_tags_by_category(self, post, categories):
        """Filter tags by selected categories."""
        if not categories or not isinstance(categories, list):
            return post.get('tag_string', '')
        parts = []
        for cat in categories:
            field = f'tag_string_{cat}'
            if field in post:
                parts.append(post[field])
        return ' '.join(parts) if parts else post.get('tag_string', '')

    def get_post(self, add_tags, max_pages=10, id=''):
        raise Exception("AIBooru does not support post IDs")


class Danbooru(Booru):

    def __init__(self):
        super().__init__('danbooru', f'https://danbooru.donmai.us/posts.json?limit={POST_AMOUNT}')

    def get_data(self, add_tags, max_pages=10, id='', tag_categories=None):
        global COUNT
        max_pages = _normalize_max_pages(max_pages)
        loop_msg = True
        for loop in range(2):
            if id:
                add_tags = ''
            random_page = random.randint(1, max(1, int(max_pages)))
            url = f"{self.base_url}&page={random_page}{add_tags}"
            self.booru_url = url
            res = self.fetch_with_retry(url, headers=self.headers, timeout=10)
            data = res.json()
            if not isinstance(data, list):
                try:
                    data = data.get('posts', [])
                except AttributeError:
                    data = []
            for post in data:
                if isinstance(post, dict):
                    post['tags'] = self._filter_tags_by_category(post, tag_categories)
            COUNT = len(data)
            if COUNT == 0:
                max_pages = 2
                while loop_msg:
                    print(f" Processing {COUNT} results.")
                    loop_msg = False
                continue
            else:
                print(f"Found enough results")
            break
        return {'post': data}

    def get_data_page(self, add_tags, page=0, id='', tag_categories=None):
        global COUNT
        if id:
            add_tags = ''
        safe_page = max(1, int(page) + 1)
        url = f"{self.base_url}&page={safe_page}{add_tags}"
        self.booru_url = url
        res = self.fetch_with_retry(url, headers=self.headers, timeout=10)
        data = res.json()
        if not isinstance(data, list):
            try:
                data = data.get('posts', [])
            except AttributeError:
                data = []
        for post in data:
            if isinstance(post, dict):
                post['tags'] = self._filter_tags_by_category(post, tag_categories)
        COUNT = len(data)
        return {'post': data}

    def _filter_tags_by_category(self, post, categories):
        """Filter tags by selected categories."""
        if not categories or not isinstance(categories, list):
            return post.get('tag_string', '')
        parts = []
        for cat in categories:
            field = f'tag_string_{cat}'
            if field in post:
                parts.append(post[field])
        return ' '.join(parts) if parts else post.get('tag_string', '')

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
    error_text = re.sub(r'((?:api_key|user_id)=)[^&\s]+', r'\1***', str(error))
    return (
        f'Ranbooru: [{booru}] request failed: {error_text}. '
        'Check proxy/TUN, lower Max Pages, or try another booru.'
    )


def _normalize_max_pages(value):
    try:
        return max(1, int(value))
    except Exception:
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
        print(f'[Ranbooru] Could not read {path}: {error}')
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


def _normalize_post(post):
    if not isinstance(post, dict):
        return None
    tags = _get_post_tags(post)
    if not tags:
        return None
    post['tags'] = tags
    file_url = _get_post_file_url(post)
    if file_url:
        post['file_url'] = file_url
    return post


def _normalize_posts(posts):
    normalized = []
    if not isinstance(posts, list):
        return normalized
    for post in posts:
        normalized_post = _normalize_post(post)
        if normalized_post is not None:
            normalized.append(normalized_post)
    return normalized


def _fetch_booru_data(api_url, booru, add_tags, max_pages, post_id='', tag_categories=None):
    if post_id:
        return api_url.get_post(add_tags, max_pages, post_id)
    if booru in ['danbooru', 'safebooru', 'aibooru', 'e621'] and tag_categories:
        return api_url.get_data(add_tags, max_pages, '', tag_categories)
    return api_url.get_data(add_tags, max_pages)


def _fetch_image(fetcher, url, headers=None):
    if not url:
        return None
    try:
        response = fetcher(url, headers=headers or {}, timeout=10)
        image = Image.open(BytesIO(response.content))
        image.load()
        return image.convert('RGB')
    except Exception as error:
        print(f'[Ranbooru] Could not fetch image {url}: {error}')
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
            print('[Ranbooru] ControlNet legacy API is unavailable; skipping Send to Controlnet.')
            return False
        controlnet_units = list(get_units(p) or [])
        if not controlnet_units:
            print('[Ranbooru] No ControlNet units found; skipping Send to Controlnet.')
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
        print(f'[Ranbooru] ControlNet send failed: {error}')
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

    booru_apis = {
        'gelbooru': Gelbooru(fringe_benefits, gelbooru_api_key, gelbooru_user_id),
        'rule34': Rule34(rule34_api_key, rule34_user_id),
        'safebooru': Safebooru(),
        'danbooru': Danbooru(),
        'konachan': Konachan(),
        'yande.re': Yandere(),
        'aibooru': AIBooru(),
        'xbooru': XBooru(),
        'e621': e621(),
    }

    api = booru_apis.get(booru_name, Gelbooru(fringe_benefits, gelbooru_api_key, gelbooru_user_id))

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
                print(f"[TagCache] 第 {page+1} 页无数据，停止抓取")
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
            print(f"[TagCache] 第 {page+1}/{start_page_index + max_pages} 页完成，获取 {len(raw_posts)} 条，规范化 {len(posts)} 条")
        except Exception as ex:
            print(f"[TagCache] 第 {page+1} 页出错: {ex}")
            break

    return records, stats


def _is_hires_second_pass(p):
    """
    尽可能识别 Hires.fix 第二段。
    """
    # A1111 常见标记
    if getattr(p, "is_hr_pass", False):
        return True
    # Forge/部分分支第二段常是 Img2Img + 有 init_images
    init_images = getattr(p, "init_images", None)
    if isinstance(p, StableDiffusionProcessingImg2Img) and init_images:
        # 如果是普通 img2img（不是 hires）也可能有 init_images，所以再加一点条件
        # 第二段通常还会有 denoising_strength / hr 相关字段
        if hasattr(p, "denoising_strength") or getattr(p, "enable_hr", False):
            return True
    return False


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
        self._local_cache_applied_jobs = set()   # 新增：记录已注入缓存的任务

    def create_prompt_row(self, i2i, component):
        self.prompt_row[i2i] = gr.Row()
        self.input_row[i2i] = gr.Row()
        self.action_row[i2i] = gr.Row()

    def set_prompt_area(self, i2i, component):
        try:
            self.prompt_area[i2i] = component.component if hasattr(component, "component") else component
        except Exception:
            self.prompt_area[i2i] = None
    previous_loras = ''
    last_img = []
    real_steps = 0
    version = "1.3"
    original_prompt = ''

    def get_files(self, path):
        files = []
        for file in os.listdir(path):
            if file.endswith('.txt'):
                files.append(file)
        return files

    def hide_object(self, obj, booru):
        print(f'hide_object: {obj}, {booru.value}')
        if booru.value == 'konachan' or booru.value == 'yande.re':
            obj.interactive = False
        else:
            obj.interactive = True

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
            credentials = credentials_manager.get_booru_credentials(booru)
            api_key = credentials.get('api_key', '')
            user_id = credentials.get('user_id', '')
            has_saved_creds = credentials_manager.has_credentials(booru)
            
            if has_saved_creds:
                # Hide input fields and show file path
                credentials_file_path = credentials_manager.credentials_file
                status_text = f"✓ Credentials loaded from: {credentials_file_path}"
                return (
                    gr.update(visible=False),  # api_key field
                    gr.update(visible=False),  # user_id field  
                    gr.update(visible=True, value=status_text),  # credentials_status
                    gr.update(visible=True, value="Clear saved credentials")  # clear_credentials_btn
                )
            else:
                # Show input fields
                return (
                    gr.update(visible=True, value=api_key),  # api_key field
                    gr.update(visible=True, value=user_id),  # user_id field
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

    def save_gelbooru_credentials(self, booru, api_key, user_id, save_credentials):
        """Save Gelbooru credentials if checkbox is checked"""
        if booru == 'gelbooru' and save_credentials and api_key.strip() and user_id.strip():
            credentials_manager.save_booru_credentials('gelbooru', api_key.strip(), user_id.strip())
            return "✓ Credentials saved"
        elif booru == 'gelbooru' and not save_credentials:
            # Optionally clear credentials if save is unchecked
            return "Credentials will not be saved"
        return ""

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
            if not records:
                return "未抓取到符合缓存过滤条件的 tag 数据", tag_cache_manager.get_status()
            if append_mode:
                save_stats = tag_cache_manager.append_records(records, dedupe=cache_dedupe)
                action = "追加完成"
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
                f"页段 {fetch_stats['start_page']}-{fetch_stats['end_page']}，"
                f"缓存总计 {save_stats['total']} 条{backup_note}"
            )
            return msg, tag_cache_manager.get_status()
        except Exception as ex:
            return f"错误: {ex}", tag_cache_manager.get_status()

    @staticmethod
    def _cache_get_next(loop_mode):
        """从缓存顺序取出下一条"""
        tags, idx, total = tag_cache_manager.get_next_tags_from_pool(loop=loop_mode)
        if tags is None:
            if total == 0:
                return "缓存为空，请先批量爬取", tag_cache_manager.get_status()
            else:
                return f"已到达缓存末尾 (索引 {idx}/{total})", tag_cache_manager.get_status()
        return tags, tag_cache_manager.get_status()

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

    @staticmethod
    def _cache_fill_by_position(position):
        """根据真实缓存序号填充标签"""
        cache_position = Script._parse_cache_id(position)
        if cache_position is None:
            return "", "请输入有效的缓存序号"
        tags = tag_cache_manager.get_by_position(cache_position)
        if tags:
            return tags, f"已取出第 {cache_position} 条"
        return "", f"未找到第 {cache_position} 条"

    @staticmethod
    def _cache_fill_position_to_tag_prompt(position, tag_prompt_text, write_mode):
        """Fill Tag Prompt with a cached tag row by visible cache position."""
        tags, msg = Script._cache_fill_by_position(position)
        if not tags:
            return "", msg, tag_prompt_text
        return tags, msg, Script._apply_write_mode(tag_prompt_text, tags, write_mode)

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
    def _cache_delete_by_position(position):
        """根据真实缓存序号删除标签"""
        cache_position = Script._parse_cache_id(position)
        if cache_position is None:
            return "请输入有效的缓存序号", tag_cache_manager.get_status()
        success = tag_cache_manager.delete_by_position(cache_position)
        if success:
            return f"已删除第 {cache_position} 条", tag_cache_manager.get_status()
        return f"删除失败：未找到第 {cache_position} 条", tag_cache_manager.get_status()

    @staticmethod
    def _cache_delete_by_tags(tags):
        if not str(tags or "").strip():
            return "请输入要删除的 tag，例如 comic,text,speech_bubble", tag_cache_manager.get_status()
        count = tag_cache_manager.delete_by_any_tags(tags)
        return f"已删除包含指定 tag 的 {count} 条缓存", tag_cache_manager.get_status()

    @staticmethod
    def _cache_refresh_status():
        pool_status = tag_cache_manager.get_filtered_pool_status()
        using_pool = tag_cache_manager.is_using_filtered_pool()
        status_msg = f"{tag_cache_manager.get_status()} | {pool_status}"
        if using_pool:
            status_msg += " [当前使用筛选池]"
        return status_msg

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
    def _cache_preview_delete_by_position(position):
        cache_position = Script._parse_cache_id(position)
        if cache_position is None:
            return "请输入有效的缓存序号"
        return Script._format_delete_preview(tag_cache_manager.preview_delete_by_positions(str(cache_position)))

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
    def _cache_get_next_and_set(loop_mode, tag_prompt_text, current_prompt, write_mode):
        """从缓存顺序取出下一条并设置到提示词"""
        tags, idx, total = tag_cache_manager.get_next_tags_from_pool(loop=loop_mode)
        if tags is None:
            if total == 0:
                return "缓存为空，请先批量爬取", tag_cache_manager.get_status(), current_prompt
            else:
                return f"已到达缓存末尾 (索引 {idx}/{total})", tag_cache_manager.get_status(), current_prompt
        tag_payload = Script._combine_prompt(tag_prompt_text, tags)
        combined = Script._apply_write_mode(current_prompt, tag_payload, write_mode)
        return tags, tag_cache_manager.get_status(), combined

    @staticmethod
    def _cache_jump_take(position, loop_mode):
        result = tag_cache_manager.jump_to_position(position)
        if not result.get("ok"):
            return result.get("message", "跳转失败"), Script._cache_refresh_status()
        return Script._cache_get_next(loop_mode)

    @staticmethod
    def _cache_jump_take_and_set(position, loop_mode, tag_prompt_text, current_prompt, write_mode):
        result = tag_cache_manager.jump_to_position(position)
        if not result.get("ok"):
            return result.get("message", "跳转失败"), Script._cache_refresh_status(), current_prompt
        return Script._cache_get_next_and_set(loop_mode, tag_prompt_text, current_prompt, write_mode)

    @staticmethod
    def _filter_tags(query, must_include, must_exclude, sort_order, preview_limit):
        """筛选标签"""
        try:
            results = tag_cache_manager.filter_tags_by_keywords(
                must_include, must_exclude, query, sort_order=sort_order, limit=int(preview_limit or 200)
            )
            if not results:
                return [], "未找到匹配的标签"
            return results, f"找到 {len(results)} 条匹配标签"
        except Exception as e:
            print(f"[Filter] Error: {e}")
            return [], f"筛选出错: {e}"

    @staticmethod
    def _create_filtered_pool(selected_ids_str):
        """创建筛选池"""
        try:
            if not selected_ids_str or not selected_ids_str.strip():
                return "请输入要添加的缓存序号（逗号分隔）", tag_cache_manager.get_filtered_pool_status()
            
            id_list = list(dict.fromkeys(int(x) for x in re.findall(r"\d+", selected_ids_str)))
            if not id_list:
                return "未找到有效的缓存序号", tag_cache_manager.get_filtered_pool_status()
            
            count = tag_cache_manager.create_filtered_pool_by_positions(id_list)
            return f"✅ 筛选池已创建，包含 {count} 条标签", tag_cache_manager.get_filtered_pool_status()
        except Exception as e:
            print(f"[FilterPool] Error: {e}")
            return f"创建失败: {e}", tag_cache_manager.get_filtered_pool_status()

    @staticmethod
    def _cache_delete_by_position_range(position_spec):
        if not str(position_spec or "").strip():
            return "请输入要删除的缓存序号或范围，例如 100-140, 150", tag_cache_manager.get_status()
        deleted, requested = tag_cache_manager.delete_by_position_spec(position_spec)
        return f"已请求 {requested} 个序号，删除 {deleted} 条缓存", tag_cache_manager.get_status()

    @staticmethod
    def _create_filtered_pool_by_range(position_spec):
        if not str(position_spec or "").strip():
            return "请输入要加入筛选池的缓存序号或范围，例如 110-180, 205", tag_cache_manager.get_filtered_pool_status()
        count, requested = tag_cache_manager.create_filtered_pool_by_position_spec(position_spec)
        return f"已请求 {requested} 个序号，筛选池包含 {count} 条", tag_cache_manager.get_filtered_pool_status()

    @staticmethod
    def _cache_export(file_format):
        try:
            result = tag_cache_manager.export_records(file_format)
            return f"已导出 {result['count']} 条 {result['format'].upper()}：{result['path']}"
        except Exception as e:
            return f"导出失败: {e}"

    @staticmethod
    def _cache_import(file_path, append_mode, dedupe):
        result = tag_cache_manager.import_records(file_path, append=append_mode, dedupe=dedupe)
        if not result.get("ok"):
            return result.get("message", "导入失败"), tag_cache_manager.get_status()
        backup_note = f"，覆盖前备份: {result.get('backup_path')}" if result.get("backup_path") else ""
        return (
            f"导入完成: 写入 {result['inserted']} 条，重复跳过 {result['skipped_duplicate']} 条，总计 {result['total']} 条{backup_note}",
            tag_cache_manager.get_status(),
        )

    @staticmethod
    def _format_import_preview(result):
        if not result.get("ok"):
            return result.get("message", "导入预检失败")
        mode = "追加" if result.get("append") else "覆盖"
        lines = [
            f"导入预检 ({mode}): 文件记录 {result.get('source_count', 0)} 条，预计写入 {result.get('inserted', 0)} 条",
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

    @staticmethod
    def _create_filtered_pool_from_filter(rule_name, query, must_include, must_exclude, sort_order, limit_count):
        """Create and activate a saved rule pool from include/exclude rules."""
        try:
            rule_id, count = tag_cache_manager.create_rule(
                rule_name, query, must_include, must_exclude, sort_order, int(limit_count or 0)
            )
            if count <= 0:
                return f"规则 #{rule_id} 已创建，但当前没有匹配条目", tag_cache_manager.get_filtered_pool_status(), tag_cache_manager.list_rules()
            return f"规则池已创建并启用：#{rule_id}，当前匹配 {count} 条", tag_cache_manager.get_filtered_pool_status(), tag_cache_manager.list_rules()
        except Exception as e:
            print(f"[FilterPool] Error: {e}")
            return f"创建失败: {e}", tag_cache_manager.get_filtered_pool_status(), tag_cache_manager.list_rules()

    @staticmethod
    def _use_filter_once(query, must_include, must_exclude, sort_order, limit_count):
        try:
            count = tag_cache_manager.create_filtered_pool_by_keywords(
                must_include, must_exclude, query, sort_order=sort_order, limit=int(limit_count or 0)
            )
            if count <= 0:
                return "当前筛选没有命中缓存条目", tag_cache_manager.get_filtered_pool_status()
            msg = tag_cache_manager.use_filtered_pool(True)
            return f"{msg}: {count} 条", tag_cache_manager.get_filtered_pool_status()
        except Exception as e:
            print(f"[FilterPool] Error: {e}")
            return f"筛选失败: {e}", tag_cache_manager.get_filtered_pool_status()

    @staticmethod
    def _list_rules():
        return tag_cache_manager.list_rules()

    @staticmethod
    def _activate_rule(rule_id):
        cache_id = Script._parse_cache_id(rule_id)
        if cache_id is None:
            return "请输入有效的规则 ID", tag_cache_manager.get_filtered_pool_status()
        return tag_cache_manager.activate_rule(cache_id), tag_cache_manager.get_filtered_pool_status()

    @staticmethod
    def _delete_rule(rule_id):
        cache_id = Script._parse_cache_id(rule_id)
        if cache_id is None:
            return "请输入有效的规则 ID", tag_cache_manager.get_filtered_pool_status(), tag_cache_manager.list_rules()
        ok = tag_cache_manager.delete_rule(cache_id)
        msg = f"已删除规则 #{cache_id}" if ok else f"未找到规则 #{cache_id}"
        return msg, tag_cache_manager.get_filtered_pool_status(), tag_cache_manager.list_rules()

    @staticmethod
    def _delete_filter_matches(query, must_include, must_exclude):
        try:
            if not any(str(value or "").strip() for value in (query, must_include, must_exclude)):
                return "请先输入搜索语法、包含或排除条件，避免误删全部缓存", tag_cache_manager.get_status()
            count = tag_cache_manager.delete_by_filter(must_include, must_exclude, query)
            return f"已删除当前筛选命中的 {count} 条缓存", tag_cache_manager.get_status()
        except Exception as e:
            return f"删除失败: {e}", tag_cache_manager.get_status()

    @staticmethod
    def _preview_filter_matches(query, must_include, must_exclude):
        if not any(str(value or "").strip() for value in (query, must_include, must_exclude)):
            return "请先输入搜索语法、包含或排除条件，避免预览全部缓存"
        return Script._format_delete_preview(tag_cache_manager.preview_delete_by_filter(must_include, must_exclude, query))

    @staticmethod
    def _switch_to_filtered_pool(use_pool):
        """切换到筛选池"""
        msg = tag_cache_manager.use_filtered_pool(use_pool)
        return msg, tag_cache_manager.get_filtered_pool_status()

    @staticmethod
    def _get_filtered_pool_status():
        """获取筛选池状态"""
        return tag_cache_manager.get_filtered_pool_status()

    def ui(self, is_img2img):
        default_booru = "safebooru"
        # Determine initial Gelbooru credential visibility based on saved credentials
        has_saved = credentials_manager.has_credentials('gelbooru')
        saved_creds = credentials_manager.get_booru_credentials('gelbooru') if has_saved else {}
        initial_api_key_visible = not has_saved
        initial_user_id_visible = not has_saved
        initial_status_visible = has_saved
        initial_status_value = f"✓ Credentials loaded from: {credentials_manager.credentials_file}" if has_saved else ""
        initial_clear_visible = has_saved
        
        row_container = self.prompt_row[is_img2img] or gr.Row()
        input_row = self.input_row[is_img2img] or gr.Row()
        action_row = self.action_row[is_img2img] or gr.Row()
        with row_container:
            with input_row:
                with gr.Column(scale=2, min_width=220):
                    tags = gr.Textbox(lines=1, label="Tags to Search (Pre)")
                with gr.Column(scale=8):
                    tag_prompt_input = gr.Textbox(lines=3, label="Tag Prompt")
            with action_row:
                with gr.Column(scale=2, min_width=220):
                    generate_prompt_btn = gr.Button("生成提示词")
                with gr.Column(scale=8):
                    with gr.Accordion(label="Ranbooru", open=False):
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
                                fringe_benefits = gr.Checkbox(label="Fringe Benefits", value=(default_booru == 'gelbooru'), visible=(default_booru == 'gelbooru'))
                                with gr.Group(visible=False) as gelbooru_credentials_group:
                                    gr.Markdown("### API Credentials")
                                    api_key = gr.Textbox(
                                        lines=1, label="API Key", placeholder="Enter your API key",
                                        type="password", visible=initial_api_key_visible, value=saved_creds.get('api_key', '')
                                    )
                                    user_id = gr.Textbox(
                                        lines=1, label="User ID", placeholder="Enter your user ID",
                                        visible=initial_user_id_visible, value=saved_creds.get('user_id', '')
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

                        # ─── Tag Cache 面板（中文 UI） ────────────────────────
                        with gr.Accordion("Tag 缓存管理", open=False):
                            gr.Markdown("### 📦 批量爬取 Tag 并缓存到本地")
                            with gr.Row():
                                cache_status_display = gr.Textbox(
                                    label="缓存状态", value=tag_cache_manager.get_status(),
                                    interactive=False, lines=1
                                )
                                cache_refresh_status_btn = gr.Button("🔄 刷新状态")

                            gr.Markdown("#### 爬取设置")
                            with gr.Row():
                                cache_pages = gr.Number(label="爬取页数", minimum=1, maximum=100, value=5, step=1, precision=0)
                                cache_start_page = gr.Number(label="从第几页开始缓存", minimum=1, maximum=100000, value=1, step=1, precision=0)
                                cache_append_mode = gr.Checkbox(label="追加模式（不覆盖已有缓存）", value=True)
                                cache_dedupe = gr.Checkbox(label="强制入库去重（完全相同 Tag 必删）", value=True, interactive=False)
                            with gr.Row():
                                cache_min_score_enabled = gr.Checkbox(label="启用最低分过滤", value=False)
                                cache_min_score = gr.Number(label="最低 Score", value=0, step=1, precision=0)
                            with gr.Row():
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
                            cache_fetch_btn = gr.Button("🚀 开始批量爬取", variant="primary")
                            cache_fetch_result = gr.Textbox(label="爬取结果", interactive=False, lines=2)

                            gr.Markdown("#### 顺序输出")
                            with gr.Row():
                                cache_loop_mode = gr.Checkbox(label="循环播放（到末尾后从头开始）", value=True)
                            with gr.Row():
                                cache_next_btn = gr.Button("▶ 取出下一条")
                                cache_next_set_btn = gr.Button("▶ 取出并设置到提示词", variant="primary")
                                cache_prompt_write_mode = gr.Dropdown(
                                    ["追加到后面", "追加到前面", "替换", "只输出"],
                                    label="写入模式",
                                    value="追加到后面"
                                )
                            with gr.Row():
                                cache_lookup_id = gr.Textbox(label="按缓存序号取 Tag", placeholder="例如: 110", lines=1)
                                cache_lookup_id_btn = gr.Button("按序号取出")
                                cache_lookup_id_set_btn = gr.Button("按序号写入 Tag Prompt")
                            with gr.Row():
                                cache_jump_position = gr.Number(label="跳转到第几条", minimum=1, step=1, precision=0)
                                cache_jump_position_btn = gr.Button("跳转到该条")
                                cache_jump_take_btn = gr.Button("跳转并取出")
                                cache_jump_take_set_btn = gr.Button("跳转并写入 Tag Prompt", variant="primary")
                            cache_jump_result = gr.Textbox(label="跳转结果", interactive=False, lines=1)
                            with gr.Row():
                                cache_preview_next_btn = gr.Button("预览下一条")
                            cache_preview_output = gr.Textbox(label="下一条预览", interactive=False, lines=3)
                            cache_next_output = gr.Textbox(label="当前取出的 Tag", interactive=False, lines=3)
                            
                            gr.Markdown("#### ⚙️ 生成设置")
                            with gr.Row():
                                use_local_cache_gen = gr.Checkbox(label="生成时使用此缓存", value=False)
                                use_local_cache_loop = gr.Checkbox(label="生成时循环读取 (到末尾自动重头)", value=True)

                        # ─── 管理操作面板 ────────────────────────
                        with gr.Accordion("缓存管理操作", open=False):
                            gr.Markdown("### 🔧 搜索、删除、重置缓存")
                            with gr.Row():
                                cache_search_keyword = gr.Textbox(label="搜索语法", placeholder="例如: +1girl +blue_eyes -2girls -text", lines=1)
                                cache_search_btn = gr.Button("🔍 搜索")
                            cache_search_results = gr.Dataframe(
                                headers=["序号", "Booru", "Post ID", "Score", "Rating", "Tags"],
                                label="搜索结果",
                                interactive=False,
                                wrap=True
                            )
                            with gr.Row():
                                cache_select_id = gr.Number(label="选择序号", minimum=1, step=1, precision=0)
                                cache_fill_btn = gr.Button("📝 填充到输出")
                                cache_preview_delete_id_btn = gr.Button("预览此条")
                                cache_delete_id_btn = gr.Button("🗑️ 删除此条", variant="stop")
                            with gr.Row():
                                cache_reset_btn = gr.Button("🔁 重置索引")
                                cache_backup_btn = gr.Button("备份缓存")
                                cache_restore_deleted_btn = gr.Button("撤销上一次删除")
                                cache_preview_delete_all_btn = gr.Button("预览全部删除")
                                cache_delete_btn = gr.Button("🗑️ 删除全部缓存", variant="stop")
                                cache_compact_btn = gr.Button("手动清除完全一致 Tag", variant="secondary")
                            with gr.Row():
                                cache_delete_tags = gr.Textbox(
                                    label="按包含 Tag 删除缓存",
                                    placeholder="例如: comic,text,speech_bubble,english_text",
                                    lines=1,
                                )
                                cache_preview_delete_tags_btn = gr.Button("预览删除这些 Tag")
                                cache_delete_tags_btn = gr.Button("删除包含这些 Tag", variant="stop")
                            with gr.Row():
                                cache_delete_range = gr.Textbox(
                                    label="按序号/范围删除缓存",
                                    placeholder="例如: 100-140, 150, 166",
                                    lines=1,
                                )
                                cache_preview_delete_range_btn = gr.Button("预览删除这些序号")
                                cache_delete_range_btn = gr.Button("删除这些序号", variant="stop")
                            cache_delete_preview = gr.Textbox(label="删除预览", interactive=False, lines=8)
                            with gr.Row():
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
                            with gr.Row():
                                cache_export_format = gr.Dropdown(["json", "csv"], label="导出格式", value="json")
                                cache_export_btn = gr.Button("导出缓存")
                            with gr.Row():
                                cache_import_path = gr.Textbox(
                                    label="导入文件路径",
                                    placeholder="例如: E:\\path\\tag_cache_export.json",
                                    lines=1,
                                )
                                cache_import_append = gr.Checkbox(label="导入时追加", value=True)
                                cache_import_dedupe = gr.Checkbox(label="导入时去重", value=True)
                                cache_import_preflight_btn = gr.Button("预检导入")
                                cache_import_btn = gr.Button("导入缓存", variant="primary")
                            cache_import_export_result = gr.Textbox(label="导入/导出结果", interactive=False, lines=8)
                            cache_manage_result = gr.Textbox(label="操作结果", interactive=False, lines=1)

                        # ─── 标签筛选池面板 ────────────────────────────────
                        with gr.Accordion("缓存筛选 / 标签筛选池", open=False):
                            gr.Markdown("### 🎯 直接筛选缓存，必要时再保存为规则池")
                            filter_query = gr.Textbox(
                                label="搜索语法",
                                placeholder="例如: +1girl +blue_eyes -2girls -text",
                                lines=1
                            )
                            
                            with gr.Row():
                                filter_must_include = gr.Textbox(
                                    label="必须包含（逗号分隔）", 
                                    placeholder="例如: 1girl,blue_eyes",
                                    lines=1
                                )
                                filter_must_exclude = gr.Textbox(
                                    label="必须排除（逗号分隔）", 
                                    placeholder="例如: 2girls,multiple_girls",
                                    lines=1
                                )
                            with gr.Row():
                                filter_sort_order = gr.Dropdown(
                                    ["ID", "Newest", "Oldest", "High Score", "Low Score", "Random"],
                                    label="规则排序",
                                    value="ID"
                                )
                                filter_preview_limit = gr.Number(label="预览数量", minimum=1, maximum=2000, value=200, step=1, precision=0)
                                filter_rule_limit = gr.Number(label="规则读取上限（0=不限）", minimum=0, maximum=100000, value=0, step=1, precision=0)
                            
                            with gr.Row():
                                filter_search_btn = gr.Button("🔍 筛选标签", variant="primary")
                                filter_quick_use_btn = gr.Button("⚡ 直接启用当前筛选", variant="secondary")
                                filter_create_rule_pool_btn = gr.Button("✅ 保存并启用规则池", variant="primary")
                            filter_result_msg = gr.Textbox(label="筛选结果", interactive=False, lines=1)
                            
                            filter_results = gr.Dataframe(
                                headers=["序号", "Booru", "Post ID", "Score", "Rating", "Tags"],
                                label="筛选预览",
                                interactive=False,
                                wrap=True
                            )

                            filter_rule_name = gr.Textbox(label="规则名称", placeholder="可留空自动生成", lines=1)
                            
                            with gr.Row():
                                filter_selected_ids = gr.Textbox(
                                    label="手动缓存序号（可选，逗号分隔）",
                                    placeholder="例如: 1,5,10,23",
                                    lines=1
                                )
                                filter_create_pool_btn = gr.Button("✅ 用手动序号创建筛选池")
                                filter_preview_delete_matches_btn = gr.Button("预览删除当前筛选")
                                filter_delete_matches_btn = gr.Button("🗑️ 删除当前筛选命中", variant="stop")
                            with gr.Row():
                                filter_range_positions = gr.Textbox(
                                    label="按序号/范围创建筛选池",
                                    placeholder="例如: 110-180, 205",
                                    lines=1,
                                )
                                filter_create_range_pool_btn = gr.Button("✅ 用范围创建筛选池")
                            
                            with gr.Row():
                                filter_pool_status = gr.Textbox(
                                    label="筛选池状态",
                                    value=tag_cache_manager.get_filtered_pool_status(),
                                    interactive=False,
                                    lines=1
                                )
                                filter_refresh_status_btn = gr.Button("🔄 刷新")
                            
                            with gr.Row():
                                filter_use_pool = gr.Checkbox(label="使用筛选池（而非主缓存）", value=tag_cache_manager.is_using_filtered_pool())
                                filter_switch_btn = gr.Button("🔄 切换")
                                filter_rule_id = gr.Textbox(label="规则 ID", placeholder="例如: 3", lines=1)
                                filter_activate_rule_btn = gr.Button("启用规则")
                                filter_delete_rule_btn = gr.Button("删除规则", variant="stop")
                            
                            filter_pool_result = gr.Textbox(label="操作结果", interactive=False, lines=1)
                            with gr.Row():
                                filter_list_rules_btn = gr.Button("刷新规则列表")
                            filter_rules = gr.Dataframe(
                                headers=["ID", "Name", "Query", "Include", "Exclude", "Sort", "Limit", "Matches"],
                                label="规则列表",
                                value=tag_cache_manager.list_rules(),
                                interactive=False,
                                wrap=True
                            )

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
            inputs=[cache_loop_mode],
            outputs=[cache_next_output, cache_status_display]
        )

        cache_lookup_id_btn.click(
            fn=self._cache_fill_by_position,
            inputs=[cache_lookup_id],
            outputs=[cache_next_output, cache_status_display]
        )

        cache_lookup_id_set_btn.click(
            fn=self._cache_fill_position_to_tag_prompt,
            inputs=[cache_lookup_id, tag_prompt_input, cache_prompt_write_mode],
            outputs=[cache_next_output, cache_status_display, tag_prompt_input]
        )

        cache_jump_position_btn.click(
            fn=self._cache_jump_to_position,
            inputs=[cache_jump_position],
            outputs=[cache_jump_result, cache_status_display]
        )

        cache_jump_take_btn.click(
            fn=self._cache_jump_take,
            inputs=[cache_jump_position, cache_loop_mode],
            outputs=[cache_next_output, cache_status_display]
        )

        cache_preview_next_btn.click(
            fn=self._cache_preview_next,
            inputs=[],
            outputs=[cache_preview_output]
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

        cache_search_btn.click(
            fn=self._cache_search,
            inputs=[cache_search_keyword],
            outputs=[cache_search_results]
        )

        cache_fill_btn.click(
            fn=self._cache_fill_by_position,
            inputs=[cache_select_id],
            outputs=[cache_next_output, cache_manage_result]
        )

        cache_delete_id_btn.click(
            fn=self._cache_delete_by_position,
            inputs=[cache_select_id],
            outputs=[cache_manage_result, cache_status_display]
        )

        cache_preview_delete_id_btn.click(
            fn=self._cache_preview_delete_by_position,
            inputs=[cache_select_id],
            outputs=[cache_delete_preview]
        )

        # ─── 筛选池事件绑定 ───────────────────────────────────────────
        filter_search_btn.click(
            fn=self._filter_tags,
            inputs=[filter_query, filter_must_include, filter_must_exclude, filter_sort_order, filter_preview_limit],
            outputs=[filter_results, filter_result_msg]
        )

        filter_create_rule_pool_btn.click(
            fn=self._create_filtered_pool_from_filter,
            inputs=[filter_rule_name, filter_query, filter_must_include, filter_must_exclude, filter_sort_order, filter_rule_limit],
            outputs=[filter_pool_result, filter_pool_status, filter_rules]
        )

        filter_quick_use_btn.click(
            fn=self._use_filter_once,
            inputs=[filter_query, filter_must_include, filter_must_exclude, filter_sort_order, filter_rule_limit],
            outputs=[filter_pool_result, filter_pool_status]
        )

        filter_create_pool_btn.click(
            fn=self._create_filtered_pool,
            inputs=[filter_selected_ids],
            outputs=[filter_pool_result, filter_pool_status]
        )

        filter_create_range_pool_btn.click(
            fn=self._create_filtered_pool_by_range,
            inputs=[filter_range_positions],
            outputs=[filter_pool_result, filter_pool_status]
        )

        filter_switch_btn.click(
            fn=self._switch_to_filtered_pool,
            inputs=[filter_use_pool],
            outputs=[filter_pool_result, filter_pool_status]
        )

        filter_activate_rule_btn.click(
            fn=self._activate_rule,
            inputs=[filter_rule_id],
            outputs=[filter_pool_result, filter_pool_status]
        )

        filter_delete_rule_btn.click(
            fn=self._delete_rule,
            inputs=[filter_rule_id],
            outputs=[filter_pool_result, filter_pool_status, filter_rules]
        )

        filter_delete_matches_btn.click(
            fn=self._delete_filter_matches,
            inputs=[filter_query, filter_must_include, filter_must_exclude],
            outputs=[filter_pool_result, cache_status_display]
        )

        filter_preview_delete_matches_btn.click(
            fn=self._preview_filter_matches,
            inputs=[filter_query, filter_must_include, filter_must_exclude],
            outputs=[cache_delete_preview]
        )

        filter_list_rules_btn.click(
            fn=self._list_rules,
            inputs=[],
            outputs=[filter_rules]
        )

        filter_refresh_status_btn.click(
            fn=self._get_filtered_pool_status,
            inputs=[],
            outputs=[filter_pool_status]
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
                inputs=[cache_loop_mode, tag_prompt_input, target_prompt_box, cache_prompt_write_mode],
                outputs=[cache_next_output, cache_status_display, target_prompt_box]
            )

            cache_jump_take_set_btn.click(
                fn=self._cache_jump_take_and_set,
                inputs=[cache_jump_position, cache_loop_mode, tag_prompt_input, target_prompt_box, cache_prompt_write_mode],
                outputs=[cache_next_output, cache_status_display, target_prompt_box]
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
                inputs=[cache_loop_mode],
                outputs=[cache_next_output, cache_status_display]
            )

            cache_jump_take_set_btn.click(
                fn=self._cache_jump_take,
                inputs=[cache_jump_position, cache_loop_mode],
                outputs=[cache_next_output, cache_status_display]
            )

        return [enabled, tags, booru, remove_bad_tags, max_pages, change_dash, same_prompt, fringe_benefits, remove_tags, use_img2img, denoising, use_last_img, change_background, change_color, shuffle_tags, post_id, mix_prompt, mix_amount, chaos_mode, negative_mode, chaos_amount, limit_tags, max_tags, sorting_order, mature_rating, lora_folder, lora_amount, lora_min, lora_max, lora_enabled, lora_custom_weights, lora_lock_prev, use_ip, use_search_txt, use_remove_txt, choose_search_txt, choose_remove_txt, search_refresh_btn, remove_refresh_btn, crop_center, use_deepbooru, type_deepbooru, use_same_seed, use_cache, api_key, user_id, save_credentials, credentials_status, clear_credentials_btn, use_local_cache_gen, use_local_cache_loop, tag_categories, cache_prompt_write_mode]

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
                    print(f'[Ranbooru] Could not list LoRA folder {lora_folder}: {error}')
                    return p
                # get only .safetensors files
                loras = [lora.replace('.safetensors', '') for lora in loras if lora.endswith('.safetensors')]
                if not loras:
                    print(f'[Ranbooru] No .safetensors LoRAs found in {lora_folder}; skipping LoRA injection.')
                    return p
                custom_weights = []
                if lora_custom_weights != '':
                    custom_weights = [value.strip() for value in lora_custom_weights.split(',')]
                for l in range(0, lora_amount):
                    lora_weight = 0
                    if l < len(custom_weights):
                        try:
                            lora_weight = float(custom_weights[l])
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

    def before_process(self, p, enabled, tags, booru, remove_bad_tags, max_pages, change_dash, same_prompt, fringe_benefits, remove_tags, use_img2img, denoising, use_last_img, change_background, change_color, shuffle_tags, post_id, mix_prompt, mix_amount, chaos_mode, negative_mode, chaos_amount, limit_tags, max_tags, sorting_order, mature_rating, lora_folder, lora_amount, lora_min, lora_max, lora_enabled, lora_custom_weights, lora_lock_prev, use_ip, use_search_txt, use_remove_txt, choose_search_txt, choose_remove_txt, search_refresh_btn, remove_refresh_btn, crop_center, use_deepbooru, type_deepbooru, use_same_seed, use_cache, api_key, user_id, save_credentials, credentials_status, clear_credentials_btn, use_local_cache_gen, use_local_cache_loop, tag_categories, *args):
        max_pages = _normalize_max_pages(max_pages)
        cache_prompt_write_mode = args[0] if args else "追加到后面"
        if use_cache:
            if HAS_REQUESTS_CACHE and not requests_cache.patcher.is_installed():
                requests_cache.install_cache('ranbooru_cache', backend='sqlite', expire_after=3600)
            elif not HAS_REQUESTS_CACHE:
                print('requests-cache not installed; running without cache')
        else:
            if HAS_REQUESTS_CACHE and requests_cache.patcher.is_installed():
                requests_cache.uninstall_cache()
        
        if enabled:
            if use_local_cache_gen:
                # 任务ID（A1111 通常有 job_timestamp）
                job_id = getattr(shared.state, "job_timestamp", None)
                if job_id is None:
                    # 兜底，避免没有 job_timestamp 时无法去重
                    job_id = f"{getattr(p, 'seed', '')}:{getattr(p, 'n_iter', '')}:{getattr(p, 'batch_size', '')}:{id(p)}"
                # 1) 先拦截 Hires.fix 第二段
                if _is_hires_second_pass(p):
                    print("[Ranbooru] 🟡 Hires.fix 第二段，跳过本地缓存取Tag（不推进索引）")
                    return
                # 2) 再做同任务防重（保险）
                if job_id in self._local_cache_applied_jobs:
                    print(f"[Ranbooru] 🟡 任务 {job_id} 已注入过缓存，跳过重复注入（不推进索引）")
                    return
                self._local_cache_applied_jobs.add(job_id)
                # 可选：防止 set 无限增长
                if len(self._local_cache_applied_jobs) > 200:
                    self._local_cache_applied_jobs.clear()
                print("[Ranbooru] 🟢 已启用本地缓存模式，执行一次缓存注入。")
                # 下面保持你原来的取缓存逻辑不变...
                
                # 计算本次批量生成的总数量 (Batch count * Batch size)
                total_images = p.batch_size * p.n_iter
                cache_prompts = []
                # 为每一张图按顺序取出一个 Tag 串
                for i in range(total_images):
                    # 从缓存管理器获取下一条（自动判断使用主缓存还是筛选池）
                    tags_str, idx, total = tag_cache_manager.get_next_tags_from_pool(loop=use_local_cache_loop)
                    
                    if tags_str is None:
                        print(f"[Ranbooru] ⚠️ 缓存已耗尽 (Index: {idx}/{total})，停止注入。")
                        tags_str = ""
                    else:
                        print(f"[Ranbooru] 📝 正在使用第 {idx} / {total} 条 Tag 数据")
                    
                    # 处理下划线
                    if change_dash:
                        tags_str = tags_str.replace("_", " ")
                    
                    cache_prompts.append(tags_str)
                # 将缓存的 Tag 追加到用户输入的 Prompt 后面
                # 如果 p.prompt 是字符串（单张），转为列表处理；如果是列表（多张），则一一对应
                if isinstance(p.prompt, list):
                    # 如果原 prompt 列表比我们生成的少，就扩展它
                    if len(p.prompt) < total_images:
                        p.prompt = p.prompt * (total_images // len(p.prompt) + 1)
                    
                    # 组合
                    new_prompts = []
                    for j in range(total_images):
                        original = p.prompt[j] if j < len(p.prompt) else ""
                        addition = cache_prompts[j]
                        combined = Script._apply_write_mode(original, addition, cache_prompt_write_mode)
                        new_prompts.append(combined)
                    p.prompt = new_prompts
                    _sync_prompt_lists(p)
                else:
                    # 单字符串情况
                    new_prompts = []
                    for j in range(total_images):
                        original = p.prompt
                        addition = cache_prompts[j]
                        combined = Script._apply_write_mode(original, addition, cache_prompt_write_mode)
                        new_prompts.append(combined)
                    p.prompt = new_prompts
                    _sync_prompt_lists(p)
                # 处理 Lora (保留原有的 Lora 逻辑)
                if lora_enabled:
                    p = self.loranado(lora_enabled, lora_folder, lora_amount, lora_min, lora_max, lora_custom_weights, p, lora_lock_prev)
                    _sync_prompt_lists(p)
                
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
            
            # Initialize APIs
            booru_apis = {
                'gelbooru': Gelbooru(fringe_benefits, gelbooru_api_key, gelbooru_user_id),
                'rule34': Rule34(rule34_api_key, rule34_user_id),
                'safebooru': Safebooru(),
                'danbooru': Danbooru(),
                'konachan': Konachan(),
                'yande.re': Yandere(),
                'aibooru': AIBooru(),
                'xbooru': XBooru(),
                'e621': e621(),
            }
            self.original_prompt = p.prompt if isinstance(p.prompt, list) else str(p.prompt or '')
            # Check if compatible
            try:
                check_exception(booru, {'tags': tags, 'post_id': post_id})
            except Exception as error:
                print(f'[Ranbooru] {error}; skipping prompt injection.')
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
                    print('No tags found in search file; skipping')

            add_tags = '&tags=-animated'
            if tags:
                add_tags += '+' + tags.replace(',', '+')
            add_tags += _get_rating_tag(booru, mature_rating)

            # Getting Data
            random_post = {'preview_url': ''}
            prompts = []
            last_img = []
            preview_urls = []
            api_url = booru_apis.get(booru, Gelbooru(fringe_benefits))
            print(f'Using {booru}')

            # Manage Post ID
            try:
                data = _fetch_booru_data(api_url, booru, add_tags, max_pages, post_id, tag_categories)
            except Exception as error:
                print(_format_ranbooru_error(booru, error))
                return p

            print(re.sub(r'((?:api_key|user_id)=)[^&\s]+', r'\1***', str(api_url.booru_url)))
            posts = _normalize_posts(data.get('post', []) if isinstance(data, dict) else [])
            if len(posts) == 0:
                if booru == 'rule34' and add_tags.startswith('&tags=-animated'):
                    fallback_add_tags = '&tags='
                    if tags:
                        fallback_add_tags += tags.replace(',', '+')
                    fallback_add_tags += _get_rating_tag(booru, mature_rating)
                    try:
                        data = api_url.get_data(fallback_add_tags, max_pages)
                    except Exception as error:
                        print(_format_ranbooru_error(booru, error))
                        return p
                    posts = _normalize_posts(data.get('post', []) if isinstance(data, dict) else [])
                if len(posts) == 0:
                    print('No posts found; skipping Ranbooru prompt injection.')
                    return p
            COUNT = len(posts)
            data = {'post': posts}
            # Replace null scores with 0s
            for post in posts:
                if isinstance(post, dict):
                    score = post.get('score')
                    try:
                        post['score'] = int(score) if score not in (None, '') else 0
                    except Exception:
                        post['score'] = 0
            # Sort based on sorting_order
            if sorting_order == 'High Score':
                data['post'] = sorted(posts, key=lambda k: (k.get('score') if isinstance(k, dict) else 0) or 0, reverse=True)
            elif sorting_order == 'Low Score':
                data['post'] = sorted(posts, key=lambda k: (k.get('score') if isinstance(k, dict) else 0) or 0)
            else:
                data['post'] = posts
            if post_id:
                print(f'Using post ID: {post_id}')
                random_numbers = [0 for _ in range(0, p.batch_size * p.n_iter)]
            else:
                random_numbers = self.random_number(sorting_order, p.batch_size * p.n_iter, len(data['post']))
            for random_number in random_numbers:
                post_index = random_numbers[0] if same_prompt else random_number
                if post_index >= len(data['post']):
                    print('[Ranbooru] Selected post index is out of range; skipping this prompt.')
                    continue
                random_post = data['post'][post_index]
                if not same_prompt and mix_prompt:
                    temp_tags = []
                    mix_max_tags = 0
                    for _ in range(0, mix_amount):
                        random_mix_number = 0 if post_id else self.random_number(sorting_order, 1, len(data['post']))[0]
                        mix_tags = _get_post_tags(data['post'][random_mix_number]).split(' ')
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
                    print(random_post)
            # Get Images
            if use_img2img or use_deepbooru:
                image_urls = [_get_post_file_url(random_post)] if use_last_img else preview_urls

                for img in image_urls:
                    image = _fetch_image(api_url.fetch_with_retry, img, headers=api_url.headers)
                    if image is not None:
                        last_img.append(image)
                if not last_img:
                    print('[Ranbooru] Could not fetch any images; disabling img2img/DeepBooru for this run.')
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
                print('No usable tags found after filtering; skipping Ranbooru prompt injection.')
                return p
            if len(prompts) == 1:
                print('Processing Single Prompt')
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
                print('Processing Multiple Prompts')
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
                    print('Padding negative prompts')
                    max_tokens = max(neg_prompt_tokens)
                    for num, neg in enumerate(neg_prompt_tokens):
                        while neg < max_tokens:
                            choices = [tag for tag in str(p.negative_prompt[num] or '').split(',') if tag]
                            if not choices:
                                break
                            p.negative_prompt[num] += random.choice(choices)
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
                    print(f'[Ranbooru] DeepBooru failed: {error}')
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

    def postprocess(self, p, processed, enabled, tags, booru, remove_bad_tags, max_pages, change_dash, same_prompt, fringe_benefits, remove_tags, use_img2img, denoising, use_last_img, change_background, change_color, shuffle_tags, post_id, mix_prompt, mix_amount, chaos_mode, negative_mode, chaos_amount, limit_tags, max_tags, sorting_order, mature_rating, lora_folder, lora_amount, lora_min, lora_max, lora_enabled, lora_custom_weights, lora_lock_prev, use_ip, use_search_txt, use_remove_txt, choose_search_txt, choose_remove_txt, search_refresh_btn, remove_refresh_btn, crop_center, use_deepbooru, type_deepbooru, use_same_seed, use_cache, api_key, user_id, save_credentials, credentials_status, clear_credentials_btn, use_local_cache_gen, use_local_cache_loop, tag_categories, *args):
        if use_img2img and not use_ip and enabled:
            print('Using pictures')
            if not hasattr(self, 'last_img') or not self.last_img:
                print('[Ranbooru] No source images available for img2img; skipping postprocess img2img.')
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
                    print(f'[Ranbooru] DeepBooru failed during postprocess: {error}')
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
        else:
            if hasattr(self, 'last_img') and self.last_img:
                for img in self.last_img:
                    processed.images.append(img)

    def generate_prompts_only(self, booru, max_pages, post_id, tags, remove_bad_tags, remove_tags, change_background, change_color, shuffle_tags, change_dash, mix_prompt, mix_amount, use_search_txt, choose_search_txt, use_remove_txt, choose_remove_txt, fringe_benefits, use_cache, api_key, user_id, save_credentials, mature_rating, sorting_order, limit_tags, max_tags, tag_categories):
        max_pages = _normalize_max_pages(max_pages)
        if use_cache:
            if HAS_REQUESTS_CACHE and not requests_cache.patcher.is_installed():
                requests_cache.install_cache('ranbooru_cache', backend='sqlite', expire_after=3600)
        else:
            if HAS_REQUESTS_CACHE and requests_cache.patcher.is_installed():
                requests_cache.uninstall_cache()

        gelbooru_api_key = None
        gelbooru_user_id = None
        rule34_api_key = None
        rule34_user_id = None
        if booru == 'gelbooru':
            if api_key.strip() and user_id.strip():
                gelbooru_api_key = api_key.strip()
                gelbooru_user_id = user_id.strip()
                if save_credentials:
                    credentials_manager.save_booru_credentials('gelbooru', gelbooru_api_key, gelbooru_user_id)
            else:
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

        booru_apis = {
            'gelbooru': Gelbooru(fringe_benefits, gelbooru_api_key, gelbooru_user_id),
            'rule34': Rule34(rule34_api_key, rule34_user_id),
            'safebooru': Safebooru(),
            'danbooru': Danbooru(),
            'konachan': Konachan(),
            'yande.re': Yandere(),
            'aibooru': AIBooru(),
            'xbooru': XBooru(),
            'e621': e621(),
        }

        # Use global DEFAULT_BAD_TAGS
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

        prompt_addition = ''
        background_options = {
            'Add Background': ('detailed_background,' + random.choice(["outdoors", "indoors"]), COLORED_BG),
            'Remove Background': ('plain_background,simple_background,' + random.choice(COLORED_BG), ADD_BG),
            'Remove All': ('', COLORED_BG + ADD_BG)
        }
        if change_background in background_options:
            pa, tags_to_remove = background_options[change_background]
            bad_tags.extend(tags_to_remove)
            prompt_addition = pa

        color_options = {
            'Colored': BW_BG,
            'Limited Palette': '(limited_palette:1.3)',
            'Monochrome': ','.join(BW_BG)
        }
        if change_color in color_options:
            co = color_options[change_color]
            if isinstance(co, list):
                bad_tags.extend(co)
            else:
                prompt_addition = f'{prompt_addition},{co}' if prompt_addition else co

        if use_search_txt:
            search_tags = _safe_read_text_file(user_search_dir, choose_search_txt)
            search_tags_r = search_tags.replace(' ', '')
            split_tags = search_tags_r.splitlines()
            filtered_tags = [line for line in split_tags if line.strip()]
            if filtered_tags:
                selected_tags = random.choice(filtered_tags)
                tags = f'{tags},{selected_tags}' if tags else selected_tags

        add_tags = '&tags=-animated'
        if tags:
            add_tags += '+' + tags.replace(',', '+')
        add_tags += _get_rating_tag(booru, mature_rating)

        api_url = booru_apis.get(booru, Gelbooru(fringe_benefits, gelbooru_api_key, gelbooru_user_id))
        try:
            data = _fetch_booru_data(api_url, booru, add_tags, max_pages, post_id, tag_categories)
        except Exception as error:
            return _format_ranbooru_error(booru, error)
        posts = _normalize_posts(data.get('post', []) if isinstance(data, dict) else [])
        if len(posts) == 0 and booru == 'rule34' and add_tags.startswith('&tags=-animated'):
            ft = '&tags='
            if tags:
                ft += tags.replace(',', '+')
            ft += _get_rating_tag(booru, mature_rating)
            try:
                data = api_url.get_data(ft, max_pages)
            except Exception as error:
                return _format_ranbooru_error(booru, error)
            posts = _normalize_posts(data.get('post', []) if isinstance(data, dict) else [])
        if len(posts) == 0:
            return NO_POSTS_MESSAGE

        for post in posts:
            if isinstance(post, dict):
                s = post.get('score')
                try:
                    post['score'] = int(s) if s not in (None, '') else 0
                except Exception:
                    post['score'] = 0
        if sorting_order == 'High Score':
            posts = sorted(posts, key=lambda k: (k.get('score') if isinstance(k, dict) else 0) or 0, reverse=True)
        elif sorting_order == 'Low Score':
            posts = sorted(posts, key=lambda k: (k.get('score') if isinstance(k, dict) else 0) or 0)

        rn = self.random_number(sorting_order, 1, len(posts))[0]
        if mix_prompt:
            temp_tags = []
            mt = 0
            for _ in range(0, mix_amount):
                rm = self.random_number(sorting_order, 1, len(posts))[0]
                mix_tags = _get_post_tags(posts[rm]).split(' ')
                temp_tags.extend(mix_tags)
                mt = max(mt, len(mix_tags))
            temp_tags = list(set(temp_tags))
            rp = posts[rn]
            mt = min(max(len(temp_tags), 20), mt)
            if temp_tags and mt > 0:
                rp['tags'] = ' '.join(random.sample(temp_tags, min(len(temp_tags), mt)))
        else:
            rp = posts[rn]

        raw_tags = _get_post_tags(rp)
        if not raw_tags:
            return NO_POSTS_MESSAGE
        temp_tags = random.sample(raw_tags.split(' '), len(raw_tags.split(' '))) if shuffle_tags else raw_tags.split(' ')
        tag_list = [t for t in temp_tags if t.strip() not in bad_tags]
        for bt in bad_tags:
            if '*' in bt:
                tag_list = [t for t in tag_list if bt.replace('*', '') not in t]
        prompt = ','.join(tag_list)
        if change_dash:
            prompt = prompt.replace('_', ' ')
        if limit_tags < 1:
            prompt = limit_prompt_tags(prompt, limit_tags, 'Limit')
        if max_tags > 0:
            prompt = limit_prompt_tags(prompt, max_tags, 'Max')
        final_prompt = f'{prompt_addition},{prompt}' if prompt_addition else prompt
        return final_prompt

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
